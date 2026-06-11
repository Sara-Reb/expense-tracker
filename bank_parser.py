import os
import json
from dotenv import load_dotenv
from pandas import read_csv, read_excel
from google import genai
from pydantic import BaseModel, Field

load_dotenv()
client = genai.Client(api_key=os.getenv("API_KEY"))


# --- STRUTTURE DATI (PIATTE E SEMPLICI) ---

class BankStatementStructure(BaseModel):
    header_row: int = Field(description="The 0-based index of the row that contains the column headers")
    date_col: str = Field(description="The exact name of the column containing the transaction date")
    amount_col: str = Field(description="The exact name of the column containing the transaction amount")
    description_col: str = Field(description="The exact name of the column containing the transaction description or merchant name")

ALLOWED_CATEGORIES = [
    "Food & Groceries", "Transport", "Housing", "Health", 
    "Shopping", "Entertainment", "Subscriptions", 
    "Education", "Travel", "Income", "Other"
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
    elif file.filename.endswith('.xlsx'):
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
    df = df.drop(df.tail(1).index)
    
    transaction_df = df[[date_col, amount_col, description_col]]
    transaction_df.columns = ['date', 'amount', 'description']
    return transaction_df

def categorize_transactions(transaction_df):
    transactions_json = transaction_df.to_json(orient='index')
    prompt = f"""You are an expert financial assistant specialized in Italian bank statement categorization.
    Your task is to analyze a list of bank transactions and map each one to a specific category and an optional merchant.

    Each transaction has a 'description' (Italian bank text) and an 'amount' (negative = expense, positive = income or refund).

    IMPORTANT RULES:
    - If the amount is POSITIVE, always assign category 'Income', regardless of the merchant name.
    - Telecom and internet providers must be categorized as 'Subscriptions', not 'Housing'.
    - Bars and cafes must be categorized as 'Food & Groceries', not 'Entertainment'.

    Here is the allowed taxonomy:
    - Food & Groceries: supermercato, ristoranti, bar, caffè, pizzerie, alimentari
    - Transport: benzina, treni, autobus, taxi, parcheggi, autostrada
    - Housing: affitto, condominio, utenze (luce, gas, acqua)
    - Health: farmacia, medici, dentista, visite, analisi, palestra, ottico
    - Shopping: abbigliamento, elettronica, acquisti generici
    - Entertainment: cinema, concerti, musei, teatro, eventi, hobby, svago
    - Subscriptions: streaming, abbonamenti telefonici, abbonamenti internet, software
    - Education: corsi, libri, università, tasse scolastiche
    - Travel: hotel, voli, vacanze, agenzie di viaggio
    - Income: bonifici in entrata, stipendio, rimborsi, qualsiasi importo positivo
    - Other: tabaccherie, prelievi contante, commissioni bancarie, tutto il resto

    Guidelines:
    1. Read the 'description' and 'amount' for each transaction carefully.
    2. Identify the 'merchant' if clearly mentioned. If generic (e.g. "Prelievo contante"), set merchant to null.
    3. Follow the rules above strictly before assigning a category.

    Input Transactions (JSON format):
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
    
    excel_path = 'I miei movimenti conto.xlsx'
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

    print("Structure:", structure)
    
    # Pulizia iniziale del file Excel
    transaction_df = parse_bank_statement(df, structure)
    
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