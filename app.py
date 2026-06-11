from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, Float, Text, Date
from bank_parser import parse_file, identify_structure, parse_bank_statement, categorize_transactions
import json
import pandas as pd

class Base(DeclarativeBase):
    pass
db = SQLAlchemy(model_class = Base)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expenses.db'
db.init_app(app)

class Expenses(db.Model):
    id : Mapped[int] = mapped_column(Integer, primary_key=True)
    date : Mapped[Date] = mapped_column(Date, nullable=False)
    category : Mapped[str] = mapped_column(Text, nullable=False)
    amount : Mapped[float] = mapped_column(Float, nullable=False)
    description : Mapped[str] = mapped_column(Text, nullable=True)
    merchant : Mapped[str] = mapped_column(Text, nullable=True)



@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    if file and (file.filename.endswith('.csv') or file.filename.endswith('.xlsx')):
        
        df = parse_file(file)
        structure = identify_structure(df)
        transaction_df = parse_bank_statement(df, structure)
        categorized_transactions = json.loads(categorize_transactions(transaction_df))
        
        categories_df = pd.DataFrame(categorized_transactions['transactions'])
        categories_df.set_index('row_id', inplace=True)
        parsed_df = transaction_df.join(categories_df)
        parsed_df.to_sql('expenses', con=db.engine, if_exists='append', index=False)
        return redirect(url_for('index'))
    else:
        message = "Invalid file format. Please upload a CSV or Excel file."
        return render_template('index.html', message=message)


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)