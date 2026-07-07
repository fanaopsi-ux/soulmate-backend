import os
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_community.vectorstores import SupabaseVectorStore
from dotenv import load_dotenv

from .ingest import get_supabase_client, get_embeddings
from .prompts import ROUTER_PROMPT, RAG_PROMPT, VTUBER_SYSTEM_PROMPT

load_dotenv()

# We use Groq as the LLM provider for fast Llama inference
def get_llm():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Please set GROQ_API_KEY in .env")
    return ChatGroq(model="llama-3.1-8b-instant", groq_api_key=api_key)

def get_retriever(category_filter: str = None):
    supabase = get_supabase_client()
    embeddings = get_embeddings()
    
    vector_store = SupabaseVectorStore(
        client=supabase,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents"
    )
    
    # Configure kwargs for metadata filtering if provided
    search_kwargs = {"k": 3} # Retrieve top 3 chunks
    if category_filter:
        search_kwargs["filter"] = {"category": category_filter}
        
    return vector_store.as_retriever(search_kwargs=search_kwargs)

def route_query(user_input: str) -> str:
    """
    Decides if the user needs advice (trigger RAG) or is just chatting.
    """
    llm = get_llm()
    prompt = PromptTemplate.from_template(ROUTER_PROMPT)
    chain = prompt | llm | StrOutputParser()
    
    # Output will be 'chat' or 'advice' based on the ROUTER_PROMPT
    decision = chain.invoke({"user_input": user_input}).strip().lower()
    return decision

def generate_rag_answer(user_input: str, category_filter: str = None) -> str:
    """
    Performs RAG to generate an answer based on textbook data.
    """
    retriever = get_retriever(category_filter)
    docs = retriever.invoke(user_input)
    
    # Combine the text from the retrieved documents
    context_text = "\n\n---\n\n".join([doc.page_content for doc in docs])
    
    llm = get_llm()
    prompt = PromptTemplate.from_template(RAG_PROMPT)
    chain = prompt | llm | StrOutputParser()
    
    # The LLM answers using ONLY the retrieved context
    answer = chain.invoke({
        "context": context_text,
        "question": user_input
    })
    
    return answer

def generate_vtuber_response(user_input: str) -> str:
    """
    Generates a normal VTuber response with the Crisis Protocol permanently active.
    """
    llm = get_llm()
    # Here we inject the Crisis Form rules directly into the system prompt
    full_prompt = f"{VTUBER_SYSTEM_PROMPT}\n\nUser: {user_input}\nVTuber:"
    
    answer = llm.invoke(full_prompt)
    return answer.content

def ask_vtuber(user_input: str, category_filter: str = None) -> str:
    """
    The main entry point. Routes the query and returns the appropriate response.
    """
    # 1. Routing
    decision = route_query(user_input)
    
    if "advice" in decision:
        print("Router decided: User needs advice. Triggering RAG.")
        # 2a. User needs help, use textbooks
        return generate_rag_answer(user_input, category_filter)
    else:
        print("Router decided: General chat.")
        # 2b. Casual chat (but Crisis Protocol is still actively monitoring via System Prompt)
        return generate_vtuber_response(user_input)

if __name__ == "__main__":
    # Simple test loop
    while True:
        q = input("You: ")
        if q.lower() in ['quit', 'exit']:
            break
        print(ask_vtuber(q))
