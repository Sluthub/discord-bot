#!/bin/bash
source venv/bin/activate
python3 -m ensurepip --default-pip
python3 -m pip install -r requirements.txt
python3 main.py
