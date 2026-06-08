from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)

# Transaction Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expenses.db'
db = SQLAlchemy(app)
class Expenses(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    category = db.Column(db.Text, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text, nullable=True)
    beneficiary = db.Column(db.Text, nullable=False)



@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    if file and (file.filename.endswith('.csv') or file.filename.endswith('.xlsx')):
        # Process the file and save transactions to the database
        # (This is a placeholder; you would implement the actual parsing logic here)
        return redirect(url_for('index'))
    else:
        message = "Invalid file format. Please upload a CSV or Excel file."
        return render_template('index.html', message=message)


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)