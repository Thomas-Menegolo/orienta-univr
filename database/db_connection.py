from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, scoped_session


Base = declarative_base()

class Database:
    """
    Classe Singleton che gestisce la connessione al database PostgreSQL.
    Ogni utente (sessione Flask) avrà una propria sessione SQLAlchemy,
    ma tutte le sessioni condividono la stessa connessione (engine).
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)

            DATABASE_URL = "postgresql+psycopg2://postgres:Giaguaro070103%40@localhost:5432/DB_App_Orientamento"

            cls._instance.engine = create_engine(
                DATABASE_URL,
                echo=False,
                pool_pre_ping=True,
            )

            cls._instance.session_factory = sessionmaker(
                bind=cls._instance.engine,
                autocommit=False,
                autoflush=False,
                expire_on_commit=False,
            )

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
