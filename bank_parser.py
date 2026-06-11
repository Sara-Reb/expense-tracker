import os
import json
from dotenv import load_dotenv
import pandas as pd
from pandas import read_csv, read_excel
from google import genai
from pydantic import BaseModel, Field
import dateparser


load_dotenv()
client = genai.Client(api_key=os.getenv("API_KEY"))


# --- STRUTTURE DATI (PIATTE E SEMPLICI) ---

class BankStatementStructure(BaseModel):
    header_row: int = Field(description="The 0-based index of the row that contains the column headers")
    date_col: str = Field(description="The exact name of the column containing the transaction date")
    amount_col: str | None = Field(description="The exact name of the column containing the transaction amount")
    income_col: str | None = Field(default=None, description="Column for income amounts, if separate from expenses")
    expense_col: str | None = Field(default=None, description="Column for expense amounts, if separate from income")
    description_col: str = Field(description="The exact name of the column containing the transaction description or merchant name")

ALLOWED_CATEGORIES = [
    "Alimentari e Ristoranti", "Trasporti e Veicoli", "Casa e Utenze", 
    "Abbonamenti e Servizi", "Salute e Spese Mediche", "Cura della Persona", 
    "Shopping e Acquisti", "Intrattenimento e Tempo Libero", "Istruzione e Formazione", 
    "Viaggi e Vacanze", "Entrate e Stipendi", "Altro"
]

class CategorizedRow(BaseModel):
    row_id: int
    merchant: str | None = None  
    category: str = Field(description=f"Must be one of: {ALLOWED_CATEGORIES}")

class TransactionAnalysis(BaseModel):
    transactions: list[CategorizedRow]


# --- FUNZIONI DI PARSING ---

def parse_file(file):
    if file.filename.endswith('.csv'):
        df = read_csv(file)
    elif file.filename.endswith('.xlsx') or file.filename.endswith('.xls'):
        df = read_excel(file)
    else:
        raise ValueError("Unsupported file format. Please upload a CSV or Excel file.")
    return df

def identify_structure(df):
    rows = df.head(80).to_string()
    prompt = f"""You are analyzing the raw contents of a bank statement file.
    Below are the first 80 rows of the file as a string.

    Your job is to identify the structure of the file.
    Note: the file is in Italian, so column headers may be in Italian (e.g. "Data", "Importo", "Descrizione").
    If there are multiple date columns (e.g. "DATA CONT." and "DATA VAL."), prefer the transaction date (contabilizzazione) over the value date (valuta).

    Raw file rows:
    {rows}
    """
    response = client.models.generate_content(
        model='gemini-3.1-flash-lite',
        contents=prompt,
        config={
            'response_mime_type': 'application/json',
            'response_schema': BankStatementStructure,
        }
    )
    return response.text

def parse_bank_statement(df, structure):
    structure = json.loads(structure)
    header_row = int(structure['header_row'])
    date_col = structure['date_col']
    amount_col = structure['amount_col']
    description_col = structure['description_col']

    df = df.drop(range(header_row)) 
    df.columns = df.iloc[0]
    df = df.drop(df.index[0])
    
    if structure.get('income_col') and structure.get('expense_col'):
        transaction_df = df[[date_col, structure['income_col'], structure['expense_col'], description_col]]
        transaction_df.columns = ['date', 'income', 'expense', 'description']
        transaction_df['income'] = pd.to_numeric(transaction_df['income'], errors='coerce').fillna(0)
        transaction_df['expense'] = pd.to_numeric(transaction_df['expense'], errors='coerce').fillna(0)
        transaction_df['amount'] = transaction_df['expense'] + transaction_df['income']
        transaction_df = transaction_df.drop(columns=['income', 'expense'])
    else:
        transaction_df = df[[date_col, amount_col, description_col]]
        transaction_df.columns = ['date', 'amount', 'description']
        transaction_df['amount'] = pd.to_numeric(transaction_df['amount'], errors='coerce')

    transaction_df = transaction_df.dropna(subset=['amount', 'date'])
    transaction_df = transaction_df[transaction_df['amount'] != 0]

    # Rimuove le righe prive di importo o data
    transaction_df = transaction_df.dropna(subset=['amount', 'date'])
    transaction_df = transaction_df[transaction_df['amount'] != 0]


    # Applichiamo il parser intelligente su ogni riga impostando la lingua italiana
    transaction_df['date'] = transaction_df['date'].apply(
        lambda x: dateparser.parse(str(x), languages=['it'])
    )

    # Rimuoviamo eventuali righe fallite (diventate NaT/None)
    transaction_df = transaction_df.dropna(subset=['date'])

    # Convertiamo nel formato standard finale YYYY-MM-DD
    transaction_df['date'] = transaction_df['date'].dt.strftime('%Y-%m-%d')
    # --------------------------------------

    return transaction_df

def categorize_transactions(transaction_df):
    transactions_json = transaction_df.to_json(orient='index')
    
    prompt = fprompt = f"""You are an expert financial assistant specialized in Italian bank statement categorization.
Your task is to analyze a list of bank transactions from ANY Italian bank and map each one to a specific category and a clean merchant name.

Each transaction has a 'description' (raw text from the bank) and an 'amount' (negative = expense, positive = income/refund).

TAXONOMY & STRICT RULES (YOU MUST USE TRADITIONAL ITALIAN CATEGORIES):
- Alimentari e Ristoranti: Supermercati, ipermercati, ristoranti, bar, caffè, pizzerie, alimentari, fast food.
- Trasporti e Veicoli: Carburante/benzina, stazioni di servizio, treni, autobus, taxi, parcheggi, pedaggi autostradali (Telepass), meccanico.
- Casa e Utenze: Affitto, spese condominiali, utenze domestiche (luce, gas, acqua, rifiuti).
- Abbonamenti e Servizi: Telecomunicazioni, internet, telefonia, pay-tv, streaming (Netflix, Spotify), abbonamenti software. NON inserire in Casa e Utenze.
- Salute e Spese Mediche: Farmacie, medici, dentisti, visite specialistiche, analisi cliniche, ottici, ticket sanitari.
- Cura della Persona: Parrucchieri, barbieri, estetisti, saloni di bellezza, centri benessere, cosmetica.
- Shopping e Acquisti: Abbigliamento, calzature, elettronica, elettrodomestici, articoli per la casa, grandi store online generici (Amazon, Temu).
- Intrattenimento e Tempo Libero: Cinema, concerti, musei, teatri, mostre, eventi, hobby, giochi, scommesse.
- Istruzione e Formazione: Corsi, libri, materiale scolastico, tasse scolastiche/universitarie.
- Viaggi e Vacanze: Hotel, voli, b&b, ostelli, pacchetti vacanze, agenzie di viaggio.
- Entrate e Stipendi: Qualsiasi importo STRETTAMENTE POSITIVO (stipendio, pensioni, bonifici in entrata, rimborsi). Se amount > 0, deve essere 'Entrate e Stipendi'.
- Altro: Tabaccherie, prelievi contante (ATM), commissioni bancarie, imposte/tasse dello stato (PagoPA, F24), e tutto ciò che non rientra nei punti precedenti.

CRITICAL INSTRUCTIONS FOR MERCHANT EXTRACTION:
1. Clean the merchant name completely: Extract ONLY the core name of the shop, utility provider, or entity.
2. Remove standard bank noise (e.g., 'Carta 9247...', 'del 24.05.2026', 'ADDEBITO SDD', 'PAGAMENTO CARTA/POS', cities like 'MILANO MI', 'ROMA ITA').
3. If the transaction is a generic bank fee (e.g., "CANONE MENSILE"), set the merchant to null.

Here are generic examples:
- INPUT: {{"date": "25 maggio", "amount": -45.00, "description": "PAGAMENTO POS 24/05 NOME_NEGOZIO MILANO CARTA N. 1234"}} -> Category: (Scegli in base a NOME_NEGOZIO), Merchant: "NOME_NEGOZIO"
- INPUT: {{"date": "15 maggio", "amount": -12.50, "description": "ADDEBITO DIRETTO SEPA SDD COMPAGNIA_LUCE_E_GAS"}} -> Category: "Casa e Utenze", Merchant: "COMPAGNIA_LUCE_E_GAS"
- INPUT: {{"date": "01 maggio", "amount": 1500.00, "description": "BONIFICO A VOSTRO FAVORE DA AZIENDA SRL STIPENDIO"}} -> Category: "Entrate e Stipendi", Merchant: "AZIENDA SRL"

Input Transactions to categorize (JSON format):
{transactions_json}
"""

    response = client.models.generate_content(
        model='gemini-3.1-flash-lite', 
        contents=prompt,
        config={
            'response_mime_type':'application/json',
            'response_schema': TransactionAnalysis,
        }
    )
    return response.text


# --- BLOCCO DI TEST ---

if __name__ == "__main__":
    import pandas as pd
    
    excel_path = 'Lista_movimenti.xls'
    structure_cache_path = 'structure_cache.json'
    analysis_cache_path = 'analysis_cache.json'  # File unico per la cache della categorizzazione
    
    df = read_excel(excel_path, header=None)
    
    # 1. Gestione Cache Struttura
    if os.path.exists(structure_cache_path):
        print("Caricamento struttura dalla cache locale...")
        with open(structure_cache_path, 'r', encoding='utf-8') as f:
            structure = f.read()
    else:
        print("Chiamata a Gemini per identificare la struttura...")
        structure = identify_structure(df)
        with open(structure_cache_path, 'w', encoding='utf-8') as f:
            f.write(structure)

    
    structure = identify_structure(df)


    print("Structure:", structure)
    
    # Pulizia iniziale del file Excel
    transaction_df = parse_bank_statement(df, structure)
    print("Estratto conto pulito:")
    print(transaction_df.to_string())
    
    # 2. Gestione Cache Categorizzazione (Identica alla precedente, a file unico!)
    if os.path.exists(analysis_cache_path):
        print("Caricamento categorizzazione dalla cache locale...")
        with open(analysis_cache_path, 'r', encoding='utf-8') as f:
            analysis_json_str = f.read()
    else:
        print("Chiamata a Gemini per la categorizzazione dei movimenti...")
        analysis_json_str = categorize_transactions(transaction_df)
        with open(analysis_cache_path, 'w', encoding='utf-8') as f:
            f.write(analysis_json_str)

    
    analysis_json_str = categorize_transactions(transaction_df)

            
    # 3. Unione e Stampa dei risultati
    analysis_data = json.loads(analysis_json_str)
    print(analysis_data)
    ai_df = pd.DataFrame(analysis_data['transactions'])
    
    if not ai_df.empty:
        ai_df.set_index('row_id', inplace=True)
        final_df = transaction_df.join(ai_df)
        print("\nEcco il tuo estratto conto finale categorizzato:")
        print(final_df[['date', 'amount', 'merchant', 'category']].to_string())
    else:
        print("Errore: Dati di analisi vuoti.")