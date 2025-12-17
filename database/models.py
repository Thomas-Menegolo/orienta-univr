from sqlalchemy import (
    Column, String, Integer, Date, ForeignKey, CheckConstraint, Text, ForeignKeyConstraint
)
from sqlalchemy.orm import relationship
from database.db_connection import Base


# 1️ Struttura
class Struttura(Base):
    __tablename__ = "struttura"

    nome = Column(String(64), primary_key=True)

    utenti_afferenti_rel = relationship("UtenteApplicazione", back_populates="struttura_afferita_rel")
    attivita_organizzate_rel = relationship("AttivitaOrientamento", back_populates="struttura_organizzante_rel")
    attivita_collaborate_rel = relationship("Collabora", back_populates="struttura_collaborante_rel")


# 2 Personale Universitario
class PersonaleUniversitario(Base):
    __tablename__ = "personale_universitario"

    email = Column(String(64), primary_key=True)
    nome = Column(String(32), nullable=False)
    cognome = Column(String(32), nullable=False)

    account_applicazione = relationship("UtenteApplicazione", uselist=False, back_populates="informazioni_personali")
    attivita_presiedute = relationship("AttivitaOrientamento", back_populates="docente_presidente_rel")
    attivita_supervisionate = relationship("Supervisiona", back_populates="docente_supervisore_rel")


# 3 Utente Applicazione
class UtenteApplicazione(Base):
    __tablename__ = "utente_applicazione"

    email = Column(String(64), ForeignKey("personale_universitario.email", onupdate="CASCADE", ondelete="CASCADE"),
                   primary_key=True)
    password = Column(String(60), nullable=False)
    ruolo = Column(String(16), nullable=False)
    struttura_afferita = Column(String(64), ForeignKey("struttura.nome", onupdate="CASCADE", ondelete="CASCADE"),
                                nullable=False)

    informazioni_personali = relationship("PersonaleUniversitario", back_populates="account_applicazione")
    struttura_afferita_rel = relationship("Struttura", back_populates="utenti_afferenti_rel")


# 4 Attività di Orientamento
class AttivitaOrientamento(Base):
    __tablename__ = "attivita_orientamento"

    id_attivita = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String(64), nullable=False)
    data_inizio = Column(Date, nullable=False)
    data_fine = Column(Date, nullable=False)
    descrizione = Column(Text)
    totale_ore = Column(Integer, CheckConstraint("totale_ore > 0"))
    struttura_organizzante = Column(String(64), ForeignKey("struttura.nome", onupdate="CASCADE", ondelete="CASCADE"),
                                    nullable=False)
    docente_presidente = Column(String(64),
                                ForeignKey("personale_universitario.email", onupdate="CASCADE", ondelete="CASCADE"),
                                nullable=False)

    __table_args__ = (
        CheckConstraint("data_fine >= data_inizio", name="check_data_fine"),
    )

    struttura_organizzante_rel = relationship("Struttura", back_populates="attivita_organizzate_rel")
    docente_presidente_rel = relationship("PersonaleUniversitario", back_populates="attivita_presiedute")
    supervisioni = relationship("Supervisiona", back_populates="attivita")
    collaborazioni = relationship("Collabora", back_populates="attivita")
    partecipazioni = relationship("Partecipa", back_populates="attivita")


# 5 Supervisiona
class Supervisiona(Base):
    __tablename__ = "supervisiona"

    id_attivita = Column(Integer,
                         ForeignKey("attivita_orientamento.id_attivita", onupdate="CASCADE", ondelete="CASCADE"),
                         primary_key=True)
    docente_supervisore = Column(String(64),
                                 ForeignKey("personale_universitario.email", onupdate="CASCADE", ondelete="CASCADE"),
                                 primary_key=True)

    attivita = relationship("AttivitaOrientamento", back_populates="supervisioni")
    docente_supervisore_rel = relationship("PersonaleUniversitario", back_populates="attivita_supervisionate")


# 6 Collabora
class Collabora(Base):
    __tablename__ = "collabora"

    id_attivita = Column(Integer,
                         ForeignKey("attivita_orientamento.id_attivita", onupdate="CASCADE", ondelete="CASCADE"),
                         primary_key=True)
    nome_struttura = Column(String(64), ForeignKey("struttura.nome", onupdate="CASCADE", ondelete="CASCADE"),
                            primary_key=True)

    attivita = relationship("AttivitaOrientamento", back_populates="collaborazioni")
    struttura_collaborante_rel = relationship("Struttura", back_populates="attivita_collaborate_rel")


class Partecipa(Base):
    __tablename__ = "partecipa"

    id_attivita = Column(Integer,
                         ForeignKey("attivita_orientamento.id_attivita", onupdate="CASCADE", ondelete="CASCADE"),
                         primary_key=True)
    codice_meccanografico = Column(String(16), primary_key=True)
    indirizzo = Column(String(64), primary_key=True)

    totale_studenti = Column(Integer, CheckConstraint("totale_studenti > 0"))
    totale_maschi = Column(Integer, CheckConstraint("totale_maschi > 0"))
    totale_femmine = Column(Integer, CheckConstraint("totale_femmine > 0"))

    # --- MODIFICA AGGIUNTA ---
    classi = Column(String(32), nullable=True)
    # --- FINE MODIFICA ---

    __table_args__ = (
        ForeignKeyConstraint(
            ["codice_meccanografico", "indirizzo"],
            ["indirizzo_scolastico.codice_meccanografico", "indirizzo_scolastico.indirizzo"],
            onupdate="CASCADE",
            ondelete="CASCADE"
        ),
    )

    attivita = relationship("AttivitaOrientamento", back_populates="partecipazioni")
    indirizzi = relationship("IndirizzoScolastico", back_populates="partecipazioni")


# 8 Personale_Scolastico
class PersonaleScolastico(Base):
    __tablename__ = "personale_scolastico"

    email = Column(String(64), primary_key=True)
    nome = Column(String(32), nullable=False)
    cognome = Column(String(32), nullable=False)

    scuola_diretta = relationship("Scuola", back_populates="info_personali_dirigente")
    indirizzo_referito = relationship("IndirizzoScolastico", back_populates="info_personali_referente")


# 9 Scuola
class Scuola(Base):
    __tablename__ = "scuola"

    codice_meccanografico = Column(String(16), primary_key=True)
    nome = Column(String(64), nullable=False)
    email = Column(String(64), nullable=False)
    numero_telefonico = Column(String(32), nullable=False)
    via = Column(String(32), nullable=False)
    numero_civico = Column(Integer, CheckConstraint("numero_civico > 0"), nullable=False)
    comune = Column(String(64), nullable=False)
    dirigente = Column(String(64), ForeignKey("personale_scolastico.email", onupdate="CASCADE", ondelete="CASCADE"),
                       nullable=False)

    info_personali_dirigente = relationship("PersonaleScolastico", back_populates="scuola_diretta")
    indirizzi = relationship("IndirizzoScolastico", back_populates="scuola")


# 10 Indirizzo_Scolastico
class IndirizzoScolastico(Base):
    __tablename__ = "indirizzo_scolastico"

    codice_meccanografico = Column(String(16),
                                   ForeignKey("scuola.codice_meccanografico", onupdate="CASCADE", ondelete="CASCADE"),
                                   primary_key=True)
    indirizzo = Column(String(64), primary_key=True)
    referente = Column(String(64), ForeignKey("personale_scolastico.email", onupdate="CASCADE", ondelete="CASCADE"),
                       nullable=False)

    scuola = relationship("Scuola", back_populates="indirizzi")
    info_personali_referente = relationship("PersonaleScolastico", back_populates="indirizzo_referito")
    partecipazioni = relationship("Partecipa", back_populates="indirizzi")