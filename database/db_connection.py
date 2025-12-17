from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, scoped_session

# Base: classe da cui ereditano tutti i modelli ORM
Base = declarative_base()

class Database:
    """
    Classe Singleton che gestisce la connessione al database PostgreSQL.
    Ogni utente (sessione Flask) avrà una propria sessione SQLAlchemy,
    ma tutte le sessioni condividono la stessa connessione (engine).
    """
    _instance = None  # Istanza unica della classe

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)

            # Stringa di connessione al DB
            DATABASE_URL = "postgresql+psycopg2://postgres:Giaguaro070103%40@localhost:5432/DB_App_Orientamento"

            # Creazione engine (connessione al DB)
            cls._instance.engine = create_engine(
                DATABASE_URL,
                echo=False,  # se True, stampa tutte le query SQL nella console
                pool_pre_ping=True,  # controlla lo stato delle connessioni inattive
            )

            # Creazione del factory di sessioni
            cls._instance.session_factory = sessionmaker(
                bind=cls._instance.engine,
                autocommit=False,
                autoflush=False,
                expire_on_commit=False,
            )

            # scoped_session: crea una sessione separata per ogni thread/utente
            cls._instance.Session = scoped_session(cls._instance.session_factory)

        return cls._instance

    def get_session(self):
        """
        Restituisce la sessione corrente (una per utente Flask).
        """
        return self.Session()

    def close_session(self):
        """
        Chiude la sessione corrente in modo sicuro.
        """
        self.Session.remove()
