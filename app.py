from datetime import datetime
import hashlib
import json
import pandas as pd

from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import Integer, Float, Text, Date, ForeignKey
from bank_parser import parse_file, identify_structure, parse_bank_statement, categorize_transactions
import os
from flask_login import LoginManager,UserMixin,login_user,logout_user,login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv




class Base(DeclarativeBase):
    pass
db = SQLAlchemy(model_class=Base)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expenses.db'
load_dotenv()
app.config['SECRET_KEY']=os.getenv('SECRET_KEY')
db.init_app(app)
login_manager = LoginManager()
login_manager.login_view='login'
login_manager.init_app(app)


class Users(db.Model,UserMixin):
    __tablename__='users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username : Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    expenses :Mapped[list['Expenses']]= relationship(backref='expenses')

@login_manager.user_loader
def load_user(user_id):
    return Users.query.get(int(user_id))


class Expenses(db.Model):
    __tablename__ = 'expenses'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer,ForeignKey('users.id'))
    date: Mapped[Date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    merchant: Mapped[str] = mapped_column(Text, nullable=True)
    transaction_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)



@app.route('/register', methods = ['GET','POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')
    else:
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')
        confirmation = request.form.get('confirmation')

        if not username:
            message = 'Nome utente richiesto'
            return render_template('/register.html', username_message=message)
        elif len(username) < 3:
            message = 'Nome utente non valido'
            return render_template('/register.html', username_message=message)
        elif not password:
            message = 'Password richiesta'
            return render_template('/register.html', password_message=message)
        elif len(password) < 8:
            message = 'Password non valida'
            return render_template('/register.html', password_message=message)
        elif password != confirmation:
            message = 'Le password non coincidono'
            return render_template('/register.html', password_message=message)
        else:
            if Users.query.filter_by(username = username).first():
                message='Username già registrato'
                return render_template('register.html', username_message = message)
            else: 
                password_hash = generate_password_hash(password)
                new_user = Users(username=username, password_hash=password_hash)
                db.session.add(new_user)
                db.session.commit()
                return redirect(url_for('login'))


@app.route('/login', methods=('GET','POST'))
def login():
    if request.method=='GET':
        return render_template('login.html')
    else:
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')

        if not username:
            message = 'Nome utente richiesto'
            return render_template('/login.html', username_message=message)
        elif not password:
            message = 'Password richiesta'
            return render_template('/login.html', password_message=message)
        else:
            user = Users.query.filter_by(username = username).first()
            if not user or not check_password_hash(user.password_hash, password):
                message='Nome utente o password non corretti'
                return render_template('login.html',check_message = message)
            else:
                login_user(user)
                return redirect(url_for('dashboard'))

@app.route('/logout')    
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
@login_required
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
        
        # 3. SALVATAGGIO RECORD (Utilizza la stessa identica formattazione dell'hash)
        for index, row in parsed_df.iterrows():
            if pd.isna(row['date']):
                continue
                
            # Estraggo la data pulita in formato YYYY-MM-DD per garantire l'uniformità dell'hash
            clean_date = row['date'].strftime('%Y-%m-%d')
            clean_amount = float(row['amount'])
            
            # Rigenero l'hash usando la stessa identica stringa standardizzata del controllo iniziale
            hash_string = f"{clean_date}_{clean_amount}_{row['description']}"
            row_hash = hashlib.md5(hash_string.encode('utf-8')).hexdigest()


            expense = Expenses(
                user_id = current_user.id,
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
@login_required
def dashboard():
    # Ordiniamo per data decrescente (le più recenti in alto)
    transactions = Expenses.query.filter_by(user_id = current_user.id).order_by(Expenses.date.desc()).all()
    return render_template('dashboard.html', transazioni=transactions)


@app.route('/api/v1/analytics')
@login_required
def api_analytics():
    try: 
        transactions = Expenses.query.filter_by(user_id = current_user.id).order_by(Expenses.date.desc()).all()

        spending_by_category = {}
        monthly_spending = {}
        monthly_income = {}

        for t in transactions:
            month = t.date.strftime('%Y-%m') 

            if t.amount < 0:
                spending_by_category[t.category] = spending_by_category.get(t.category, 0) + abs(t.amount)
                monthly_spending[month] = monthly_spending.get(month, 0) + abs(t.amount)
            else:
                monthly_income[month] = monthly_income.get(month, 0) + t.amount
        
        spending_by_category = {k: round(v, 2) for k, v in spending_by_category.items()}
        monthly_spending = {k: round(v, 2) for k, v in monthly_spending.items()}
        monthly_income = {k: round(v, 2) for k, v in monthly_income.items()}

        return jsonify({
            "status": "success",
            'data':{
                'spending_by_category': spending_by_category,
                'monthly_analytics':{
                    'months':sorted(list(set(list(monthly_spending.keys())))),
                    'spending':monthly_spending,
                    'income':monthly_income
                }
            }
        }),200
    
    except Exception as e:
        return jsonify({
            'status':'error',
            'message': str(e)
        }), 500


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)