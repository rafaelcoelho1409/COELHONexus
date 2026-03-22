from fastapi import APIRouter, HTTPException
from langchain_openai import ChatOpenAI
from neo4j import GraphDatabase


router = APIRouter()

# =============================================================================
# Variables
# =============================================================================


# =============================================================================
# Endpoints
# =============================================================================
@router.get("/clear_neo4j_graph")
def clear_neo4j_graph():
    driver = GraphDatabase.driver(
        os.environ["NEO4J_HOST"], 
        auth = (
            os.environ["NEO4J_USERNAME"], 
            os.environ["NEO4J_PASSWORD"],
            )
        )
    with driver.session(database = "neo4j") as session:
        session.run("MATCH (n) DETACH DELETE n")
    return "All previous Neo4J relationships cleared to avoid context confusion."