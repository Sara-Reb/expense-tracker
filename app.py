from datetime import datetime
import hashlib
import json
import pandas as pd

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, Float, Text, Date
from bank_parser import parse_file, identify_structure, parse_bank_statement, categorize_transactions
import os

class Base(DeclarativeBase):
    pass
db = SQLAlchemy(model_class=Base)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expenses.db'
db.init_app(app)

class Expenses(db.Model):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    merchant: Mapped[str] = mapped_column(Text, nullable=True)
    transaction_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    if not file or not(file.filename.endswith('.csv') or file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        message = "Invalid file format. Please upload a CSV or Excel file."
        return render_template('index.html', message=message)
    try:
        df = parse_file(file)
        structure = identify_structure(df)
        struct_dict = json.loads(structure)
        for key in ['date_col', 'amount_col', 'description_col', 'income_col', 'expense_col']:
            if struct_dict.get(key):
                struct_dict[key] = struct_dict[key].strip()
        structure = json.dumps(struct_dict)
        transaction_df = parse_bank_statement(df, structure)

        # 1. CONTROLLO DUPLICATI (Standardizzato)
        new_rows = []
        for index, row in transaction_df.iterrows():
            clean_date = pd.to_datetime(row['date']).strftime('%Y-%m-%d')
            clean_amount = float(row['amount'])
            hash_string = f"{clean_date}_{clean_amount}_{row['description']}"
            row_hash = hashlib.md5(hash_string.encode('utf-8')).hexdigest()
            
            exists = Expenses.query.filter_by(transaction_hash=row_hash).first()
            if not exists:
                new_rows.append(index)
                
        transaction_df = transaction_df.loc[new_rows].reset_index(drop=True)

        if transaction_df.empty:
            return redirect(url_for('dashboard'))

        # 2. CATEGORIZZAZIONE GEMINI
        categorized_transactions = json.loads(categorize_transactions(transaction_df))
        categories_df = pd.DataFrame(categorized_transactions['transactions'])
        categories_df.set_index('row_id', inplace=True)
        parsed_df = transaction_df.join(categories_df)
        
        parsed_df['date'] = pd.to_datetime(parsed_df['date'], errors='coerce')
        
        # 3. SALVATAGGIO RECORDI (Utilizza la stessa identica formattazione dell'hash)
        for index, row in parsed_df.iterrows():
            if pd.isna(row['date']):
                continue
                
            # Estraggo la data pulita in formato YYYY-MM-DD per garantire l'uniformità dell'hash
            clean_date = row['date'].strftime('%Y-%m-%d')
            clean_amount = float(row['amount'])
            
            # Rigenero l'hash usando la stessa identica stringa standardizzata del controllo iniziale
            hash_string = f"{clean_date}_{clean_amount}_{row['description']}"
            row_hash = hashlib.md5(hash_string.encode('utf-8')).hexdigest()

            # Controllo difensivo dell'ultimo secondo per evitare crash in qualsiasi situazione
            exists_last_minute = Expenses.query.filter_by(transaction_hash=row_hash).first()
            if exists_last_minute:
                continue

            expense = Expenses(
                date=row['date'].date(),
                category=row['category'],
                amount=clean_amount,
                description=row['description'],
                merchant=row['merchant'] if pd.notna(row['merchant']) else None,
                transaction_hash=row_hash
            )
            db.session.add(expense)
        
        db.session.commit()
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Errore durante l'upload: {e}")
        message = "Si è verificato un errore durante l'elaborazione del file. Riprova"
        return render_template('index.html', message=message)
    

@app.route('/dashboard')
def dashboard():
    # Ordiniamo per data decrescente (le più recenti in alto)
    transactions = Expenses.query.order_by(Expenses.date.desc()).all()
    spending_by_category = {}
    for t in transactions:
        if t.amount < 0:
            spending_by_category[t.category] = spending_by_category.get(t.category, 0) + abs(t.amount)
    monthly_spending = {}
    monthly_income = {}
    for t in transactions:
        month = t.date.strftime('%Y-%m') 
        if t.amount < 0:
            monthly_spending[month] = monthly_spending.get(month, 0) + abs(t.amount)
        else:
            monthly_income[month] = monthly_income.get(month, 0) + t.amount
    
    return render_template('dashboard.html', transazioni=transactions, spending_by_category=spending_by_category, monthly_spending=monthly_spending, monthly_income=monthly_income)


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)