from pandas import read_csv, read_excel
from dotenv import load_dotenv
from google import genai
import os
import json

load_dotenv()
client = genai.Client(api_key=os.getenv("API_KEY"))


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

    Your job is to identify the structure of the file and return ONLY a JSON object with no additional text, no markdown, no backticks.

    The JSON must have exactly these fields:
    - "header_row": the 0-based index of the row that contains the column headers (e.g. date, amount, description)
    - "date_col": the exact name of the column containing the transaction date
    - "amount_col": the exact name of the column containing the transaction amount
    - "description_col": the exact name of the column containing the transaction description or merchant name

    Note: the file is in Italian, so column headers may be in Italian (e.g. "Data", "Importo", "Descrizione").
    If there are multiple date columns (e.g. "DATA CONT." and "DATA VAL."), prefer the transaction date (contabilizzazione) over the value date (valuta).

    Raw file rows:
    {rows}
    """
    interaction = client.interactions.create(
        model = 'gemini-3.5-flash',
        input = prompt
    )
    return interaction.output_text

def parse_bank_statement(df, structure):
    structure = json.loads(structure)
    header_row = structure['header_row']
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


if __name__ == "__main__":
    import pandas as pd
    df = read_excel('I miei movimenti conto.xlsx', header=None)
    structure = identify_structure(df)
    print("Identified structure:", structure)
    transaction_df = parse_bank_statement(df, structure)
    print(transaction_df)
