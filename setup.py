"""Instalación del paquete contable-auto."""
from setuptools import setup, find_packages

setup(
    name="contable-auto",
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
            "contable-auto=main:cli",
        ],
    },
)
