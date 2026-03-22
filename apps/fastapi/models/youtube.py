import os
from typing import Any
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv

load_dotenv(override = True)

