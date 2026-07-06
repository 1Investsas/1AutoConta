"""Instalación del paquete 1ContaBot."""
from setuptools import setup, find_packages

setup(
    name="1contabot",
    version="1.0.0",
    description="Automatización contable de facturación electrónica colombiana para 1 INVEST SAS",
    author="1 INVEST SAS",
    packages=find_packages(),
    install_requires=[
        "pandas>=2.0",
        "openpyxl>=3.1",
        "click>=8.0",
        "rich>=13.0",
        "python-dotenv>=1.0",
        "sqlalchemy>=2.0",
    ],
    python_requires=">=3.11",
    entry_points={
        "console_scripts": [
            "1contabot=main:cli",
        ],
    },
)
