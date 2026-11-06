# Buduje dist\MHO900Decoder.exe (nie wymaga Pythona na komputerze docelowym).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
}
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name MHO900Decoder `
    --exclude-module tkinter --exclude-module matplotlib --exclude-module PySide2 --exclude-module PySide6 `
    --exclude-module PyQt6 --exclude-module scipy --exclude-module pytest `
    --add-data "mhodecode\srd_decoders;mhodecode\srd_decoders" `
    --hidden-import binascii --hidden-import calendar --hidden-import collections --hidden-import copy --hidden-import ctypes `
    --hidden-import decimal --hidden-import enum --hidden-import functools --hidden-import itertools `
    --hidden-import json --hidden-import math --hidden-import operator --hidden-import platform `
    --hidden-import re --hidden-import string --hidden-import struct --hidden-import subprocess --hidden-import zlib `
    main.py
