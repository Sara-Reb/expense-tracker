from datetime import datetime
import hashlib
import json
import pandas as pd

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, Float, Text, Date
from bank_parser import parse_file, identify_structure, parse_bank_statement, categorize_transactions

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
    if file and (file.filename.endswith('.csv') or file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        
        df = parse_file(file)
        structure = identify_structure(df)
        struct_dict = json.loads(structure)
        for key in ['date_col', 'amount_col', 'description_col', 'income_col', 'expense_col']:
            if struct_dict.get(key):
                struct_dict[key] = struct_dict[key].strip()
        structure = json.dumps(struct_dict)
        transaction_df = parse_bank_statement(df, structure)
        categorized_transactions = json.loads(categorize_transactions(transaction_df))
        
        categories_df = pd.DataFrame(categorized_transactions['transactions'])
        categories_df.set_index('row_id', inplace=True)
        parsed_df = transaction_df.join(categories_df)
        
        # Sforziamo pandas a normalizzare tutta la colonna 'date' prima di ciclare
        parsed_df['date'] = pd.to_datetime(parsed_df['date'], errors='coerce')
        
        # UN UNICO CICLO FOR CORRETTO
        for index, row in parsed_df.iterrows():
            
            # 2. CREAZIONE DELL'HASH UNIVOCO
            hash_string = f"{row['date']}_{row['amount']}_{row['description']}"
            row_hash = hashlib.md5(hash_string.encode('utf-8')).hexdigest()

            # Controllo duplicati
            exists = Expenses.query.filter_by(transaction_hash=row_hash).first()
            if exists:
                continue

            # 3. Salvataggio del record
            expense = Expenses(
                date=row['date'].date() if pd.notna(row['date']) else None,
                category=row['category'],
                amount=float(row['amount']),
                description=row['description'],
                merchant=row['merchant'] if pd.notna(row['merchant']) else None,
                transaction_hash=row_hash
            )
            db.session.add(expense)
        
        db.session.commit()
        return redirect(url_for('dashboard'))
    else:
        message = "Invalid file format. Please upload a CSV or Excel file."
        return render_template('index.html', message=message)
    

@app.route('/dashboard')
def dashboard():
    # Ordiniamo per data decrescente (le più recenti in alto)
    transazioni = Expenses.query.order_by(Expenses.date.desc()).all()
    return render_template('dashboard.html', transazioni=transazioni)


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)