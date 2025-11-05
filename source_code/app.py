import streamlit as st
from google import genai
import psycopg2
import os
import json # Included just in case we need to handle JSON data later, but not strictly necessary for the core logic now.

# --- Configuration (Moved from Flask) ---
# METHOD 1 (Recommended for testing): Directly paste your API Key here.
# REMEMBER TO REMOVE THIS BEFORE SHARING!
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]# Replace with a real key or use Streamlit Secrets

# Your PostgreSQL Configuration
# Note: st.secrets is a dictionary, you can access nested values
DB_NAME = st.secrets["POSTGRES_DB"]["dbname"]
DB_USER = st.secrets["POSTGRES_DB"]["user"]
DB_PASS = st.secrets["POSTGRES_DB"]["password"]
DB_HOST = st.secrets["POSTGRES_DB"]["host"]
DB_PORT = st.secrets["POSTGRES_DB"]["port"]

DATABASE_URL = f"dbname={DB_NAME} user={DB_USER} password={DB_PASS} host={DB_HOST} port={DB_PORT}"

# Initialize Gemini Client
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except ValueError:
    st.error("FATAL ERROR: Gemini API Key is missing or invalid. Please check your configuration.")
    st.stop() # Stop the app if the key is bad

# --- Database Schema Retrieval (Moved from Flask) ---
@st.cache_data(show_spinner=False) # Cache the schema since it rarely changes
def get_db_schema():
    """Fetches the structure of the database tables."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # This query fetches table names and their column definitions
        cur.execute("""
            SELECT 
                table_name, 
                column_name, 
                data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position;
        """)
        
        schema = {}
        for table, column, data_type in cur.fetchall():
            if table not in schema:
                schema[table] = []
            schema[table].append(f"{column} ({data_type})")

        schema_text = "\n\n".join([
            f"Table: {t}\nColumns: {', '.join(c)}" for t, c in schema.items()
        ])
        return schema_text
        
    except (Exception, psycopg2.Error) as error:
        # In a Streamlit app, we capture the error but let the chat function handle the display
        return f"Schema unavailable due to database error: {error}"
    finally:
        if conn:
            conn.close()

# --- Core Chat Logic Function (Combined Flask /chat logic) ---

def generate_db_answer(user_question, db_schema):
    """
    1. Prompts Gemini to generate SQL.
    2. Executes the SQL query against PostgreSQL.
    3. Prompts Gemini to generate a natural language answer from the result.
    """
    if "Schema unavailable" in db_schema:
         return f"Sorry, I can't connect to the database to retrieve the schema. Error: {db_schema}"
    
    try:
        # 1. Prompt Gemini to generate SQL
        sql_generation_prompt = f"""
        You are an expert SQL generator for a PostgreSQL database.
        
        The database schema is:\n{db_schema}\n
        
        Based on the user's question, generate *only* the single best SQL query. 
        Do not add any explanation, text, or markdown, just the SQL.
        Question: "{user_question}"
        """
        
        response_sql = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=sql_generation_prompt,
        )
        # Clean the generated SQL (remove markdown blocks)
        sql_query = response_sql.text.strip().replace("```sql", "").replace("```", "").strip()
        
    except Exception as e:
        return f"Error generating SQL: {e}"

    # 2. Execute SQL Query
    conn = None
    query_result = "No data found."
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(sql_query)
        
        # Fetch data and column names
        column_names = [desc[0] for desc in cur.description]
        data = cur.fetchall()
        
        # Format result for the LLM
        result_rows = [dict(zip(column_names, row)) for row in data]
        query_result = str(result_rows) # Convert the list of dicts to a string

    except psycopg2.Error as e:
        query_result = f"Database Error: {e}"
    finally:
        if conn:
            conn.close()

    # 3. Prompt Gemini to generate the final natural language answer
    final_answer_prompt = f"""
    The user asked: "{user_question}"
    
    The SQL query executed was: "{sql_query}"
    
    The database result is:\n{query_result}\n
    
    Analyze the result and provide a clear, concise, natural language answer to the user's original question.
    If the result is a Database Error or shows 'No data found.', inform the user politely.
    """
    
    try:
        response_answer = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=final_answer_prompt,
        )
        return response_answer.text

    except Exception as e:
        return f"Error generating final answer: {e}"


# --- Streamlit Frontend Setup ---

st.set_page_config(
    page_title="Supermarket Analysis Chatbot",
    page_icon="🤖"
)

st.title("🤖 Supermarket Chatbot")
st.caption("Ask questions about the supermarket data !")

# Get the database schema once
DB_SCHEMA = get_db_schema()

if "messages" not in st.session_state:
    st.session_state["messages"] = [
        {"role": "assistant", "content": "Hello, Sir/ Ma'am ! I can answer your questions by querying the database. What would you like to know?"}
    ]

# Display all previous messages in the chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Handle new user input
if prompt := st.chat_input("Enter your question..."):
    # 1. Add user message to history and display it
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Generate the full answer using the core function
    with st.chat_message("assistant"):
        with st.spinner("Thinking... (Generating answer from database)"):
            answer = generate_db_answer(prompt, DB_SCHEMA)
            
            # Display the final answer
            st.markdown(answer)

    # 3. Add assistant response to history
    st.session_state.messages.append({"role": "assistant", "content": answer})

st.sidebar.title("Configuration")
if "Schema unavailable" in DB_SCHEMA:
    st.sidebar.error("Database Schema Retrieval Failed!")
    st.sidebar.markdown(f"**Error Details:**\n```\n{DB_SCHEMA}\n```")
else:
    st.sidebar.success("Database Schema Loaded Successfully!")
    st.sidebar.expander("View Schema").code(DB_SCHEMA, language='text')

st.sidebar.info("Tip: For production, store your `GEMINI_API_KEY` and `DATABASE_URL` securely using Streamlit Secrets.")