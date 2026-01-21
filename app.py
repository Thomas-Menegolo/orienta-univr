import re
import os
from datetime import timedelta, date, datetime
from collections import Counter, defaultdict
from functools import wraps
import io
import csv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response

from bcrypt import checkpw, hashpw, gensalt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, subqueryload
from sqlalchemy import func, extract, distinct, or_, case

from database.db_connection import Database
from database.models import (
    UtenteApplicazione, AttivitaOrientamento, Collabora, PersonaleUniversitario,
    Struttura, Scuola, IndirizzoScolastico, Supervisiona, Partecipa,
    PersonaleScolastico
)

app = Flask(__name__)
# Usare una chiave sicura da variabile d'ambiente
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'CHIAVE_SEGRETA_DI_SVILUPPO')
app.permanent_session_lifetime = timedelta(minutes=30)


@app.teardown_appcontext
def shutdown_session(exception=None):
    """Chiude la sessione del database al termine di ogni richiesta."""
    Database().close_session()


def login_required(f):
    """Decoratore per proteggere le rotte richiedendo che l'utente sia loggato."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def parse_partecipanti_form(form_data):
    partecipanti = []
    temp_data = {}
    scuole_map = {}
    regex_scuola_id = re.compile(r'scuole\[(\d+)\]\[id\]')
    regex_indirizzo = re.compile(r'scuole\[(\d+)\]\[indirizzi\]\[(\d+)\]\[(\w+)\]')

    for key, values in form_data.lists():
        match_s = regex_scuola_id.match(key)
        if match_s:
            scuole_map[int(match_s.group(1))] = values[0]
            continue
        match_i = regex_indirizzo.match(key)
        if match_i:
            idx_s, idx_i, field = int(match_i.group(1)), int(match_i.group(2)), match_i.group(3)
            if idx_s not in temp_data: temp_data[idx_s] = {}
            if idx_i not in temp_data[idx_s]: temp_data[idx_s][idx_i] = {}
            if field == 'classi': temp_data[idx_s][idx_i][field] = ", ".join(values)
            else: temp_data[idx_s][idx_i][field] = values[0] if values else None

    for idx_s, indirizzi in temp_data.items():
        cm = scuole_map.get(idx_s)
        if not cm: continue
        for dati in indirizzi.values():
            if not dati.get('id'): continue
            def to_int_or_none(val): return int(val) if val and val.strip() else None
            partecipanti.append({
                'codice_meccanografico': cm,
                'indirizzo': dati.get('id'),
                'totale_studenti': to_int_or_none(dati.get('tot')),
                'totale_maschi': to_int_or_none(dati.get('maschi')),
                'totale_femmine': to_int_or_none(dati.get('femmine')),
                'altro': to_int_or_none(dati.get('altro')),
                'classi': dati.get('classi') or None
            })
    return partecipanti


def salva_attivita_db(session_db, form_data, attivita_esistente=None):
    """Gestisce il salvataggio (Inserimento o Update) di un'attività nel DB."""
    try:
        is_new = attivita_esistente is None
        attivita = attivita_esistente if not is_new else AttivitaOrientamento()

        attivita.nome = form_data.get('titolo')
        attivita.descrizione = form_data.get('descrizione') or None
        attivita.data_inizio = form_data.get('data_inizio')
        attivita.data_fine = form_data.get('data_fine')
        attivita.totale_ore = int(form_data.get('totale_ore')) if form_data.get('totale_ore') else None
        attivita.docente_presidente = form_data.get('referente')
        attivita.struttura_organizzante = form_data.get('dip_organizzante')

        if is_new:
            session_db.add(attivita)

        session_db.flush()

        if not is_new:
            session_db.query(Supervisiona).filter_by(id_attivita=attivita.id_attivita).delete()
            session_db.query(Collabora).filter_by(id_attivita=attivita.id_attivita).delete()
            session_db.query(Partecipa).filter_by(id_attivita=attivita.id_attivita).delete()

        for email in set(form_data.getlist('supervisori[]')):
            if email:
                session_db.add(Supervisiona(id_attivita=attivita.id_attivita, docente_supervisore=email))

        for strut in set(form_data.getlist('collaboratori[]')):
            if strut:
                session_db.add(Collabora(id_attivita=attivita.id_attivita, nome_struttura=strut))

        for p in parse_partecipanti_form(form_data):
            session_db.add(Partecipa(id_attivita=attivita.id_attivita, **p))

        session_db.commit()
        return True, None
    except IntegrityError:
        session_db.rollback()
        return False, "Errore di integrità nel database."
    except Exception as e:
        session_db.rollback()
        return False, f"Errore imprevisto: {str(e)}"


def get_common_options(session_db):
    """Recupera liste per select."""
    p_raw = session_db.query(PersonaleUniversitario).order_by(PersonaleUniversitario.cognome,
                                                              PersonaleUniversitario.nome).all()
    s_raw = session_db.query(Scuola).order_by(Scuola.nome).all()
    strutture = session_db.query(Struttura).order_by(Struttura.nome).all()

    p_counts = Counter((p.nome.lower(), p.cognome.lower()) for p in p_raw)
    p_opt = [{'id': p.email, 'text': f"{p.cognome} {p.nome} - {p.email}" if p_counts[(
        p.nome.lower(), p.cognome.lower())] > 1 else f"{p.cognome} {p.nome}"} for p in p_raw]

    s_counts = Counter(s.nome.lower() for s in s_raw)
    s_opt = [{'id': s.codice_meccanografico,
              'text': f"{s.nome} - {s.codice_meccanografico}" if s_counts[s.nome.lower()] > 1 else s.nome} for s in
             s_raw]
    return p_opt, strutture, s_opt


def upsert_personale_scolastico(session_db, email, nome, cognome):
    """Gestisce inserimento/check personale scolastico."""
    personale = session_db.query(PersonaleScolastico).get(email)
    if personale:
        if personale.nome.lower() == nome.lower() and personale.cognome.lower() == cognome.lower():
            return True, None
        else:
            error_msg = f"L'email '{email}' è già associata a '{personale.nome} {personale.cognome}'."
            return False, error_msg
    else:
        nuovo_personale = PersonaleScolastico(
            email=email,
            nome=nome,
            cognome=cognome
        )
        session_db.add(nuovo_personale)
        return True, None


def format_supervisori(supervisioni):
    if not supervisioni:
        return ""

    nomi = [f"{s.docente_supervisore_rel.cognome} {s.docente_supervisore_rel.nome}" for s in supervisioni]
    counts = Counter(nomi)

    output = []
    for s in supervisioni:
        nome_completo = f"{s.docente_supervisore_rel.cognome} {s.docente_supervisore_rel.nome}"
        if counts[nome_completo] > 1:
            output.append(f"{nome_completo} - {s.docente_supervisore_rel.email}")
        else:
            output.append(nome_completo)
    return ", ".join(sorted(list(set(output))))


# ==============================================================================
# ROTTE PRINCIPALI
# ==============================================================================

@app.route('/', methods=['GET', 'POST'])
def login():
    error = None
    session_db = Database().get_session()
    if request.method == 'POST':
        email, password = request.form['email'], request.form['password']
        utente = session_db.query(UtenteApplicazione).filter_by(email=email).first()
        if utente and checkpw(password.encode('utf-8'), utente.password.encode('utf-8')):
            session.permanent = True
            session['user'] = utente.email
            session['ruolo'] = utente.ruolo
            session['struttura'] = utente.struttura_afferita
            return redirect(url_for('attivita'))
        else:
            error = "Email o password non corretti"
    return render_template('login.html', error=error)


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/attivita')
@login_required
def attivita():
    session_db = Database().get_session()
    oggi, struttura = date.today(), session.get('struttura')
    ordine = (
        AttivitaOrientamento.data_inizio.asc(), AttivitaOrientamento.data_fine.asc(), AttivitaOrientamento.nome.asc())
    if struttura == "Ateneo di Verona":
        prog = session_db.query(AttivitaOrientamento).filter(AttivitaOrientamento.data_inizio > oggi).order_by(*ordine)
        svolte = session_db.query(AttivitaOrientamento).filter(AttivitaOrientamento.data_inizio <= oggi).order_by(
            *ordine)
    else:
        base = session_db.query(AttivitaOrientamento).outerjoin(Collabora,
                                                                Collabora.id_attivita == AttivitaOrientamento.id_attivita).filter(
            (AttivitaOrientamento.struttura_organizzante == struttura) | (Collabora.nome_struttura == struttura))
        prog = base.filter(AttivitaOrientamento.data_inizio > oggi).order_by(*ordine)
        svolte = base.filter(AttivitaOrientamento.data_inizio <= oggi).order_by(*ordine)
    return render_template('attivita.html',
                           attivita_svolte=[
                               {'id': a.id_attivita, 'titolo': a.nome, 'inizio': a.data_inizio.strftime("%d/%m/%Y"),
                                'fine': a.data_fine.strftime("%d/%m/%Y")} for a in svolte],
                           attivita_programmate=[
                               {'id': a.id_attivita, 'titolo': a.nome, 'inizio': a.data_inizio.strftime("%d/%m/%Y"),
                                'fine': a.data_fine.strftime("%d/%m/%Y")} for a in prog],
                           struttura=struttura, user=session['user'])


@app.route('/inserisci_attivita', methods=['GET', 'POST'])
@login_required
def inserisci_attivita():
    session_db = Database().get_session()
    if request.method == 'POST':
        success, msg = salva_attivita_db(session_db, request.form)
        if success:
            return redirect(url_for('attivita'))
        else:
            return f"Errore inserimento: {msg}", 400
    p_opt, strutture, s_opt = get_common_options(session_db)
    return render_template('form_attivita.html', modalita='inserisci', attivita=None, personale_opt=p_opt,
                           strutture=strutture, scuole_opt=s_opt)


@app.route('/modifica_attivita/<int:id_attivita>', methods=['GET', 'POST'])
@login_required
def modifica_attivita(id_attivita):
    session_db = Database().get_session()
    attivita = session_db.query(AttivitaOrientamento).get(id_attivita)
    if not attivita: return "Attività non trovata", 404
    if session['ruolo'] != 'Ufficio Orientamento' and attivita.struttura_organizzante != session['struttura']:
        return "Non hai i permessi per modificare questa attività", 403

    if request.method == 'POST':
        success, msg = salva_attivita_db(session_db, request.form, attivita_esistente=attivita)
        if success:
            return redirect(url_for('attivita'))
        else:
            return f"Errore modifica: {msg}", 400
    dati_attivita = {
        'id': attivita.id_attivita, 'titolo': attivita.nome, 'descrizione': attivita.descrizione,
        'totale_ore': attivita.totale_ore, 'data_inizio': attivita.data_inizio.isoformat(),
        'data_fine': attivita.data_fine.isoformat(), 'referente': attivita.docente_presidente,
        'dip_organizzante': attivita.struttura_organizzante,
        'supervisori': [s.docente_supervisore for s in attivita.supervisioni],
        'collaboratori': [c.nome_struttura for c in attivita.collaborazioni]
    }
    part_map = {}
    for p in attivita.partecipazioni:
        if p.codice_meccanografico not in part_map: part_map[p.codice_meccanografico] = []
        part_map[p.codice_meccanografico].append(
            {'indirizzo': p.indirizzo, 'tot': p.totale_studenti, 'maschi': p.totale_maschi,
             'femmine': p.totale_femmine, 'altro': p.altro, 'classi': p.classi})
    p_opt, strutture, s_opt = get_common_options(session_db)
    return render_template('form_attivita.html', modalita='modifica', attivita=dati_attivita,
                           scuole_preload=[{'id_scuola': cm, 'indirizzi': inds} for cm, inds in part_map.items()],
                           personale_opt=p_opt, strutture=strutture, scuole_opt=s_opt)


@app.route('/cancella_attivita/<int:id_attivita>', methods=['POST'])
@login_required
def cancella_attivita(id_attivita):
    session_db = Database().get_session()
    attivita = session_db.query(AttivitaOrientamento).get(id_attivita)

    if not attivita:
        return jsonify({'success': False, 'error': 'Attività non trovata'}), 404
    if session['ruolo'] != 'Ufficio Orientamento' and attivita.struttura_organizzante != session['struttura']:
        return jsonify({'success': False, 'error': 'Non hai i permessi per cancellare questa attività'}), 403

    try:
        session_db.query(Supervisiona).filter_by(id_attivita=id_attivita).delete()
        session_db.query(Collabora).filter_by(id_attivita=id_attivita).delete()
        session_db.query(Partecipa).filter_by(id_attivita=id_attivita).delete()

        session_db.delete(attivita)
        session_db.commit()
        return jsonify({'success': True})
    except Exception as e:
        session_db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/indirizzi/<codice_scuola>')
@login_required
def get_indirizzi_scuola(codice_scuola):
    session_db = Database().get_session()
    indirizzi = session_db.query(IndirizzoScolastico).filter_by(codice_meccanografico=codice_scuola).order_by(
        IndirizzoScolastico.indirizzo).all()
    return jsonify([{'indirizzo': i.indirizzo} for i in indirizzi])


# --- LISTE SEMPLICI ---

@app.route('/personale')
@login_required
def personale_universitario():
    session_db = Database().get_session()
    personale = session_db.query(PersonaleUniversitario).order_by(
        PersonaleUniversitario.cognome.asc(),
        PersonaleUniversitario.nome.asc()
    ).all()
    return render_template('personale.html',
                           personale_list=personale,
                           struttura=session.get('struttura'))


@app.route('/scuole')
@login_required
def scuole():
    session_db = Database().get_session()
    scuole_list = session_db.query(Scuola).join(
        PersonaleScolastico, Scuola.dirigente == PersonaleScolastico.email
    ).order_by(Scuola.nome.asc()).all()
    return render_template('scuola.html',
                           scuole_list=scuole_list,
                           struttura=session.get('struttura'))


@app.route('/indirizzi')
@login_required
def indirizzi_scolastici():
    session_db = Database().get_session()
    indirizzi_list = session_db.query(IndirizzoScolastico).join(
        Scuola, IndirizzoScolastico.codice_meccanografico == Scuola.codice_meccanografico
    ).join(
        PersonaleScolastico, IndirizzoScolastico.referente == PersonaleScolastico.email
    ).order_by(Scuola.nome.asc(), IndirizzoScolastico.indirizzo.asc()).all()
    return render_template('indirizzi.html',
                           indirizzi_list=indirizzi_list,
                           struttura=session.get('struttura'))


# --- CRUD PERSONALE ---

@app.route('/personale/inserisci', methods=['GET', 'POST'])
@login_required
def inserisci_personale():
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return "Non autorizzato", 403

    error = None
    return_to = request.values.get('return_to')
    return_id = request.values.get('return_id')

    if request.method == 'POST':
        email = request.form.get('email')
        try:
            existing_personale = session_db.query(PersonaleUniversitario).get(email)
            if existing_personale:
                raise IntegrityError(
                    f"L'email '{email}' è già associata a '{existing_personale.nome} {existing_personale.cognome}'.",
                    params=None, orig=None)

            nuovo_personale = PersonaleUniversitario(
                email=email,
                nome=request.form['nome'],
                cognome=request.form['cognome']
            )
            session_db.add(nuovo_personale)
            session_db.commit()

            if return_to:
                if return_id:
                    return redirect(url_for(return_to, id_attivita=int(return_id)))
                else:
                    return redirect(url_for(return_to))
            return redirect(url_for('personale_universitario'))

        except IntegrityError as e:
            session_db.rollback()
            if e.orig:
                error = str(e.orig.args[0]).capitalize()
            else:
                error = f"L'email '{email}' è già esistente."
            return render_template('form_personale.html', modalita='inserisci', personale=None, error=error,
                                   form_data=request.form, return_to=return_to, return_id=return_id)
        except Exception as e:
            session_db.rollback()
            error = str(e).capitalize()
            return render_template('form_personale.html', modalita='inserisci', personale=None, error=error,
                                   form_data=request.form, return_to=return_to, return_id=return_id)

    return render_template('form_personale.html', modalita='inserisci', personale=None, error=error, form_data=None,
                           return_to=return_to, return_id=return_id)


@app.route('/personale/modifica/<string:email>', methods=['GET', 'POST'])
@login_required
def modifica_personale(email):
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return "Non autorizzato", 403

    personale = session_db.query(PersonaleUniversitario).get(email)
    if not personale:
        return "Personale non trovato", 404

    error = None
    return_to = request.values.get('return_to')
    return_id = request.values.get('return_id')

    if request.method == 'POST':
        try:
            personale.nome = request.form['nome']
            personale.cognome = request.form['cognome']
            session_db.commit()

            if return_to:
                if return_id:
                    return redirect(url_for(return_to, id_attivita=int(return_id)))
                else:
                    return redirect(url_for(return_to))
            return redirect(url_for('personale_universitario'))

        except Exception as e:
            session_db.rollback()
            error = str(e).capitalize()

    return render_template('form_personale.html',
                           modalita='modifica',
                           personale=personale,
                           error=error,
                           form_data=request.form if error else None,
                           return_to=return_to,
                           return_id=return_id)


@app.route('/personale/cancella/<string:email>', methods=['POST'])
@login_required
def cancella_personale(email):
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return jsonify({'success': False, 'error': 'Non autorizzato'}), 403

    personale = session_db.query(PersonaleUniversitario).get(email)
    if not personale:
        return jsonify({'success': False, 'error': 'Personale non trovato'}), 404

    try:
        session_db.delete(personale)
        session_db.commit()
        return jsonify({'success': True})
    except IntegrityError:
        session_db.rollback()
        return jsonify(
            {'success': False, 'error': 'Impossibile cancellare: questo utente è referenziato in altre tabelle.'}), 400
    except Exception as e:
        session_db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# --- CRUD SCUOLA ---

@app.route('/scuole/inserisci', methods=['GET', 'POST'])
@login_required
def inserisci_scuola():
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return "Non autorizzato", 403

    error = None
    return_to = request.values.get('return_to')
    return_id = request.values.get('return_id')

    if request.method == 'POST':
        try:
            numero_completo = request.form['numero_telefonico']
            if not numero_completo or not numero_completo.startswith('+'):
                raise Exception("Numero di telefono non valido o mancante.")

            success, error_msg = upsert_personale_scolastico(
                session_db,
                request.form['dirigente_email'],
                request.form['dirigente_nome'],
                request.form['dirigente_cognome']
            )

            if not success:
                raise Exception(error_msg)

            nuova_scuola = Scuola(
                codice_meccanografico=request.form['codice_meccanografico'],
                nome=request.form['nome'],
                email=request.form['email'],
                numero_telefonico=numero_completo,
                via=request.form['via'],
                numero_civico=int(request.form['numero_civico']),
                comune=request.form['comune'],
                dirigente=request.form['dirigente_email']
            )
            session_db.add(nuova_scuola)
            session_db.commit()

            if return_to:
                if return_id:
                    return redirect(url_for(return_to, id_attivita=int(return_id)))
                else:
                    return redirect(url_for(return_to))
            return redirect(url_for('scuole'))

        except IntegrityError:
            session_db.rollback()
            error = "Codice Meccanografico già esistente."
        except Exception as e:
            session_db.rollback()
            error = str(e).capitalize()

    return render_template('form_scuola.html',
                           modalita='inserisci',
                           scuola=None,
                           error=error,
                           form_data=request.form if error else None,
                           return_to=return_to,
                           return_id=return_id)


@app.route('/scuole/modifica/<string:cm>', methods=['GET', 'POST'])
@login_required
def modifica_scuola(cm):
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return "Non autorizzato", 403

    scuola = session_db.query(Scuola).get(cm)
    if not scuola:
        return "Scuola non trovata", 404

    error = None
    return_to = request.values.get('return_to')
    return_id = request.values.get('return_id')

    if request.method == 'POST':
        try:
            numero_completo = request.form['numero_telefonico']
            if not numero_completo or not numero_completo.startswith('+'):
                raise Exception("Numero di telefono non valido o mancante.")

            success, error_msg = upsert_personale_scolastico(
                session_db,
                request.form['dirigente_email'],
                request.form['dirigente_nome'],
                request.form['dirigente_cognome']
            )

            if not success:
                raise Exception(error_msg)

            scuola.nome = request.form['nome']
            scuola.email = request.form['email']
            scuola.numero_telefonico = numero_completo
            scuola.via = request.form['via']
            scuola.numero_civico = int(request.form['numero_civico'])
            scuola.comune = request.form['comune']
            scuola.dirigente = request.form['dirigente_email']

            session_db.commit()

            if return_to:
                if return_id:
                    return redirect(url_for(return_to, id_attivita=int(return_id)))
                else:
                    return redirect(url_for(return_to))
            return redirect(url_for('scuole'))
        except Exception as e:
            session_db.rollback()
            error = str(e).capitalize()

    return render_template('form_scuola.html',
                           modalita='modifica',
                           scuola=scuola,
                           error=error,
                           form_data=request.form if error else None,
                           return_to=return_to,
                           return_id=return_id)


@app.route('/scuole/cancella/<string:cm>', methods=['POST'])
@login_required
def cancella_scuola(cm):
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return jsonify({'success': False, 'error': 'Non autorizzato'}), 403

    scuola = session_db.query(Scuola).get(cm)
    if not scuola:
        return jsonify({'success': False, 'error': 'Scuola non trovata'}), 404

    try:
        session_db.delete(scuola)
        session_db.commit()
        return jsonify({'success': True})
    except IntegrityError:
        session_db.rollback()
        return jsonify(
            {'success': False, 'error': 'Impossibile cancellare. Verifica che non ci siano riferimenti pendenti.'}), 400
    except Exception as e:
        session_db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# --- CRUD INDIRIZZI ---

@app.route('/indirizzi/inserisci', methods=['GET', 'POST'])
@login_required
def inserisci_indirizzo():
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return "Non autorizzato", 403

    error = None
    return_to = request.values.get('return_to')
    return_id = request.values.get('return_id')

    if request.method == 'POST':
        try:
            success, error_msg = upsert_personale_scolastico(
                session_db,
                request.form['referente_email'],
                request.form['referente_nome'],
                request.form['referente_cognome']
            )

            if not success:
                raise Exception(error_msg)

            nuovo_indirizzo = IndirizzoScolastico(
                codice_meccanografico=request.form['codice_meccanografico'],
                indirizzo=request.form['indirizzo'],
                referente=request.form['referente_email']
            )
            session_db.add(nuovo_indirizzo)
            session_db.commit()

            if return_to:
                if return_id:
                    return redirect(url_for(return_to, id_attivita=int(return_id)))
                else:
                    return redirect(url_for(return_to))
            return redirect(url_for('indirizzi_scolastici'))

        except IntegrityError:
            session_db.rollback()
            error = "Questo indirizzo esiste già per la scuola selezionata (chiave duplicata)."
        except Exception as e:
            session_db.rollback()
            error = str(e).capitalize()

    _, _, scuole_opt = get_common_options(session_db)
    return render_template('form_indirizzo.html',
                           modalita='inserisci',
                           indirizzo_data=None,
                           scuole_opt=scuole_opt,
                           error=error,
                           form_data=request.form if error else None,
                           return_to=return_to,
                           return_id=return_id)


@app.route('/indirizzi/modifica/<string:cm>/<path:indirizzo>', methods=['GET', 'POST'])
@login_required
def modifica_indirizzo(cm, indirizzo):
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return "Non autorizzato", 403

    pk = (cm, indirizzo)
    indirizzo_data = session_db.get(IndirizzoScolastico, pk)

    if not indirizzo_data:
        return "Indirizzo non trovato", 404

    error = None
    return_to = request.values.get('return_to')
    return_id = request.values.get('return_id')

    if request.method == 'POST':
        try:
            success, error_msg = upsert_personale_scolastico(
                session_db,
                request.form['referente_email'],
                request.form['referente_nome'],
                request.form['referente_cognome']
            )

            if not success:
                raise Exception(error_msg)

            indirizzo_data.referente = request.form['referente_email']

            session_db.commit()

            if return_to:
                if return_id:
                    return redirect(url_for(return_to, id_attivita=int(return_id)))
                else:
                    return redirect(url_for(return_to))
            return redirect(url_for('indirizzi_scolastici'))
        except Exception as e:
            session_db.rollback()
            error = str(e).capitalize()

    _, _, scuole_opt = get_common_options(session_db)
    return render_template('form_indirizzo.html',
                           modalita='modifica',
                           indirizzo_data=indirizzo_data,
                           scuole_opt=scuole_opt,
                           error=error,
                           form_data=request.form if error else None,
                           return_to=return_to,
                           return_id=return_id)


@app.route('/indirizzi/cancella/<string:cm>/<path:indirizzo>', methods=['POST'])
@login_required
def cancella_indirizzo(cm, indirizzo):
    session_db = Database().get_session()
    if session.get('ruolo') != 'Ufficio Orientamento' and session.get('struttura') == 'Ateneo di Verona':
        return jsonify({'success': False, 'error': 'Non autorizzato'}), 403

    pk = (cm, indirizzo)
    indirizzo_data = session_db.get(IndirizzoScolastico, pk)

    if not indirizzo_data:
        return jsonify({'success': False, 'error': 'Indirizzo non trovato'}), 404

    try:
        session_db.delete(indirizzo_data)
        session_db.commit()
        return jsonify({'success': True})
    except IntegrityError:
        session_db.rollback()
        return jsonify(
            {'success': False, 'error': 'Impossibile cancellare: questo indirizzo è referenziato nelle attività.'}), 400
    except Exception as e:
        session_db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# --- CRUD REFERENTI (UTENTI APP - SOLO UFFICIO ORIENTAMENTO) ---

@app.route('/referenti')
@login_required
def lista_referenti():
    if session.get('ruolo') != 'Ufficio Orientamento':
        return "Accesso Negato", 403

    session_db = Database().get_session()
    referenti = session_db.query(UtenteApplicazione).order_by(UtenteApplicazione.email).all()

    return render_template('referenti.html', referenti_list=referenti, struttura=session.get('struttura'))


@app.route('/referenti/inserisci', methods=['GET', 'POST'])
@login_required
def inserisci_referente():
    if session.get('ruolo') != 'Ufficio Orientamento':
        return "Accesso Negato", 403

    session_db = Database().get_session()
    error = None

    if request.method == 'POST':
        try:
            email = request.form['email']
            nome = request.form['nome']
            cognome = request.form['cognome']
            password = request.form['password']
            confirm_password = request.form['confirm_password']
            ruolo = request.form['ruolo']
            struttura_afferita = request.form['struttura_afferita']

            if password != confirm_password:
                raise Exception("Le password non coincidono.")

            personale = session_db.query(PersonaleUniversitario).get(email)

            if personale:
                if personale.nome.lower() != nome.lower() or personale.cognome.lower() != cognome.lower():
                    raise Exception("Questa email è già associata ad un altro utente (nome/cognome diversi).")
            else:
                nuovo_personale = PersonaleUniversitario(email=email, nome=nome, cognome=cognome)
                session_db.add(nuovo_personale)
                session_db.flush()

            hashed_pw = hashpw(password.encode('utf-8'), gensalt()).decode('utf-8')

            nuovo_utente = UtenteApplicazione(
                email=email,
                password=hashed_pw,
                ruolo=ruolo,
                struttura_afferita=struttura_afferita
            )
            session_db.add(nuovo_utente)
            session_db.commit()

            return redirect(url_for('lista_referenti'))

        except IntegrityError:
            session_db.rollback()
            error = "Utente già esistente."
        except Exception as e:
            session_db.rollback()
            error = str(e)

    strutture = session_db.query(Struttura).order_by(Struttura.nome).all()
    return render_template('form_referente.html',
                           modalita='inserisci',
                           referente=None,
                           strutture=strutture,
                           error=error,
                           struttura=session.get('struttura'))


@app.route('/referenti/modifica/<string:email>', methods=['GET', 'POST'])
@login_required
def modifica_referente(email):
    if session.get('ruolo') != 'Ufficio Orientamento':
        return "Accesso Negato", 403

    session_db = Database().get_session()
    referente = session_db.query(UtenteApplicazione).options(joinedload(UtenteApplicazione.informazioni_personali)).get(email)

    if not referente:
        return "Referente non trovato", 404

    is_self = (email == session.get('user'))
    error = None

    if request.method == 'POST':
        try:
            if not is_self:
                nome = request.form['nome']
                cognome = request.form['cognome']
                ruolo = request.form['ruolo']
                struttura_afferita = request.form['struttura_afferita']

                if referente.informazioni_personali:
                    referente.informazioni_personali.nome = nome
                    referente.informazioni_personali.cognome = cognome

                referente.ruolo = ruolo
                referente.struttura_afferita = struttura_afferita

            new_password = request.form.get('password')
            if new_password and new_password.strip():
                hashed_pw = hashpw(new_password.encode('utf-8'), gensalt()).decode('utf-8')
                referente.password = hashed_pw

            session_db.commit()
            return redirect(url_for('lista_referenti'))

        except Exception as e:
            session_db.rollback()
            error = str(e)

    strutture = session_db.query(Struttura).order_by(Struttura.nome).all()
    return render_template('form_referente.html',
                           modalita='modifica',
                           referente=referente,
                           strutture=strutture,
                           error=error,
                           is_self=is_self,
                           struttura=session.get('struttura'))


@app.route('/referenti/cancella/<string:email>', methods=['POST'])
@login_required
def cancella_referente(email):
    if session.get('ruolo') != 'Ufficio Orientamento':
        return jsonify({'success': False, 'error': 'Non autorizzato'}), 403

    session_db = Database().get_session()
    referente = session_db.query(UtenteApplicazione).get(email)

    if not referente:
        return jsonify({'success': False, 'error': 'Referente non trovato'}), 404

    if referente.email == session.get('user'):
        return jsonify({'success': False, 'error': 'Non puoi cancellare il tuo stesso account.'}), 400

    try:
        session_db.delete(referente)
        session_db.commit()
        return jsonify({'success': True})
    except Exception as e:
        session_db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# --- ROTTE RESOCONTO ATTIVITA' (DETTAGLIO) E EXPORT ---

@app.route('/resoconto_attivita/<int:id_attivita>')
@login_required
def resoconto_attivita(id_attivita):
    session_db = Database().get_session()

    query = session_db.query(AttivitaOrientamento).options(
        joinedload(AttivitaOrientamento.docente_presidente_rel),
        joinedload(AttivitaOrientamento.supervisioni).joinedload(Supervisiona.docente_supervisore_rel),
        joinedload(AttivitaOrientamento.collaborazioni),
        joinedload(AttivitaOrientamento.partecipazioni).joinedload(Partecipa.indirizzi).joinedload(
            IndirizzoScolastico.scuola).joinedload(Scuola.info_personali_dirigente),
        joinedload(AttivitaOrientamento.partecipazioni).joinedload(Partecipa.indirizzi).joinedload(
            IndirizzoScolastico.info_personali_referente)
    ).filter(AttivitaOrientamento.id_attivita == id_attivita)

    attivita = query.first()

    if not attivita:
        return "Attività non trovata", 404

    supervisori_str = format_supervisori(attivita.supervisioni)
    collaboratori_str = ", ".join(sorted(list(set([c.nome_struttura for c in attivita.collaborazioni]))))

    part_map = {}
    for p in attivita.partecipazioni:
        if not p.indirizzi or not p.indirizzi.scuola:
            continue

        scuola = p.indirizzi.scuola
        if scuola.codice_meccanografico not in part_map:
            part_map[scuola.codice_meccanografico] = {
                'scuola_data': scuola,
                'indirizzi': []
            }
        part_map[scuola.codice_meccanografico]['indirizzi'].append(p)

    base_query = session_db.query(Partecipa).filter(Partecipa.id_attivita == id_attivita)

    q_scuole = base_query.join(IndirizzoScolastico).join(Scuola).with_entities(
        Scuola.nome,
        Scuola.codice_meccanografico,
        func.sum(func.coalesce(Partecipa.totale_studenti, 0)).label('totale')
    ).group_by(Scuola.nome, Scuola.codice_meccanografico).all()

    nomi_scuole = [s.nome for s in q_scuole]
    counts_nomi = Counter(nomi_scuole)
    chart_scuole_labels = [f"{s.nome} - {s.codice_meccanografico}" if counts_nomi[s.nome] > 1 else s.nome for s in q_scuole]
    chart_scuole_data = [s.totale for s in q_scuole]

    q_indirizzi = base_query.join(IndirizzoScolastico).with_entities(
        IndirizzoScolastico.indirizzo,
        func.sum(func.coalesce(Partecipa.totale_studenti, 0)).label('totale')
    ).group_by(IndirizzoScolastico.indirizzo).order_by(
        func.sum(func.coalesce(Partecipa.totale_studenti, 0)).desc()).all()

    chart_indirizzi_labels = [i.indirizzo for i in q_indirizzi]
    chart_indirizzi_data = [i.totale for i in q_indirizzi]

    q_sesso = base_query.with_entities(
        func.sum(func.coalesce(Partecipa.totale_studenti, 0)),
        func.sum(func.coalesce(Partecipa.totale_maschi, 0)),
        func.sum(func.coalesce(Partecipa.totale_femmine, 0))
    ).first()

    tot_studenti = q_sesso[0] or 0
    tot_m = q_sesso[1] or 0
    tot_f = q_sesso[2] or 0
    tot_altro = tot_studenti - (tot_m + tot_f)

    chart_sesso_labels = ['Maschi', 'Femmine', 'Altro']
    chart_sesso_data = [tot_m, tot_f, tot_altro]

    dati_disponibili = tot_studenti > 0

    return render_template('resoconto_attivita.html',
                           attivita=attivita,
                           supervisori_str=supervisori_str,
                           collaboratori_str=collaboratori_str,
                           part_map=part_map,
                           dati_disponibili=dati_disponibili,
                           chart_scuole_labels=chart_scuole_labels,
                           chart_scuole_data=chart_scuole_data,
                           chart_indirizzi_labels=chart_indirizzi_labels,
                           chart_indirizzi_data=chart_indirizzi_data,
                           chart_sesso_labels=chart_sesso_labels,
                           chart_sesso_data=chart_sesso_data,
                           struttura=session.get('struttura'),
                           tot_studenti=tot_studenti,
                           tot_m=tot_m,
                           tot_f=tot_f,
                           tot_altro=tot_altro)


@app.route('/export/report.csv')
@login_required
def export_report():
    if session.get('struttura') != 'Ateneo di Verona':
        return "Non autorizzato", 403

    db = Database().get_session()

    query = db.query(AttivitaOrientamento).options(
        joinedload(AttivitaOrientamento.docente_presidente_rel),
        joinedload(AttivitaOrientamento.supervisioni).joinedload(Supervisiona.docente_supervisore_rel),
        joinedload(AttivitaOrientamento.collaborazioni),
        joinedload(AttivitaOrientamento.partecipazioni).joinedload(Partecipa.indirizzi).joinedload(
            IndirizzoScolastico.scuola)
    )

    attivita_list = query.all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    header = [
        'ID Attivita', 'Nome Attivita', 'Data Inizio', 'Data Fine', 'Descrizione', 'Totale Ore',
        'Dipartimento Organizzante', 'Dipartimenti Collaboranti', 'Docente Referente', 'Docenti Supervisori',
        'Scuola Codice', 'Scuola Nome', 'Indirizzo', 'Classi', 'Totale Studenti', 'Totale Maschi', 'Totale Femmine',
        'Altro'
    ]
    writer.writerow(header)

    for a in attivita_list:
        referente = f"{a.docente_presidente_rel.cognome} {a.docente_presidente_rel.nome} - {a.docente_presidente_rel.email}"
        collaboratori = ", ".join(sorted(list(set([c.nome_struttura for c in a.collaborazioni]))))
        supervisori = format_supervisori(a.supervisioni)

        base_row = [
            a.id_attivita, a.nome, a.data_inizio.isoformat(), a.data_fine.isoformat(), a.descrizione, a.totale_ore,
            a.struttura_organizzante, collaboratori, referente, supervisori
        ]

        if not a.partecipazioni:
            writer.writerow(base_row + [''] * 8)
        else:
            for p in a.partecipazioni:
                scuola_codice = p.codice_meccanografico
                scuola_nome = p.indirizzi.scuola.nome if p.indirizzi and p.indirizzi.scuola else 'N/D'

                m = p.totale_maschi or 0
                f = p.totale_femmine or 0
                alt = p.totale_studenti - (m + f)

                partecipazione_row = [
                    scuola_codice, scuola_nome, p.indirizzo, p.classi,
                    p.totale_studenti, m, f, alt
                ]
                writer.writerow(base_row + partecipazione_row)

    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers["Content-Disposition"] = "attachment; filename=report_attivita.csv"
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    return response


# --- DASHBOARD GENERALE (/resoconto) ---

@app.route('/resoconto')
@login_required
def resoconto():
    session_db = Database().get_session()
    struttura = session.get('struttura')

    year_filter = request.args.get('year', 'storico')
    dip_filter = request.args.get('dip_filter')

    all_dips = []
    if struttura == 'Ateneo di Verona':
        all_dips = [r[0] for r in session_db.query(Struttura.nome).order_by(Struttura.nome).all()]

    if struttura == 'Ateneo di Verona':
        if dip_filter and dip_filter != 'Ateneo di Verona':
            base_query = session_db.query(AttivitaOrientamento).outerjoin(Collabora,
                                                                          Collabora.id_attivita == AttivitaOrientamento.id_attivita).filter(
                (AttivitaOrientamento.struttura_organizzante == dip_filter) | (Collabora.nome_struttura == dip_filter)
            )
        else:
            base_query = session_db.query(AttivitaOrientamento)
    else:
        base_query = session_db.query(AttivitaOrientamento).outerjoin(Collabora,
                                                                      Collabora.id_attivita == AttivitaOrientamento.id_attivita).filter(
            (AttivitaOrientamento.struttura_organizzante == struttura) | (Collabora.nome_struttura == struttura)
        )

    if year_filter != 'storico':
        try:
            target_year = int(year_filter)
            base_query = base_query.filter(extract('year', AttivitaOrientamento.data_inizio) == target_year)
        except ValueError:
            pass

    today = date.today()
    kpi_svolte = base_query.filter(AttivitaOrientamento.data_fine < today).count()
    kpi_programmate = base_query.filter(AttivitaOrientamento.data_fine >= today).count()
    kpi_attivita = kpi_svolte + kpi_programmate

    kpi_ore = base_query.with_entities(func.sum(AttivitaOrientamento.totale_ore)).scalar() or 0

    stmt_ids = base_query.with_entities(AttivitaOrientamento.id_attivita)
    partecipazioni_q = session_db.query(Partecipa).filter(Partecipa.id_attivita.in_(stmt_ids))

    kpi_studenti = partecipazioni_q.with_entities(func.sum(Partecipa.totale_studenti)).scalar() or 0
    kpi_scuole = partecipazioni_q.with_entities(
        func.count(func.distinct(Partecipa.codice_meccanografico))).scalar() or 0
    kpi_indirizzi = partecipazioni_q.join(IndirizzoScolastico).with_entities(
        func.count(func.distinct(IndirizzoScolastico.indirizzo))).scalar() or 0

    q_scuole = partecipazioni_q.join(IndirizzoScolastico).join(Scuola).with_entities(
        Scuola.nome,
        func.sum(func.coalesce(Partecipa.totale_maschi, 0)),
        func.sum(func.coalesce(Partecipa.totale_femmine, 0)),
        func.sum(func.coalesce(Partecipa.totale_studenti, 0)),
        func.count(distinct(Partecipa.id_attivita))
    ).group_by(Scuola.nome).order_by(func.sum(Partecipa.totale_studenti).desc()).limit(10).all()

    chart_scuole = {
        'labels': [s[0] for s in q_scuole],
        'maschi': [s[1] for s in q_scuole],
        'femmine': [s[2] for s in q_scuole],
        'altro': [(s[3] - (s[1] + s[2])) for s in q_scuole],
        'attivita': [s[4] for s in q_scuole]
    }

    q_indirizzi = partecipazioni_q.join(IndirizzoScolastico).with_entities(
        IndirizzoScolastico.indirizzo,
        func.sum(func.coalesce(Partecipa.totale_maschi, 0)),
        func.sum(func.coalesce(Partecipa.totale_femmine, 0)),
        func.sum(func.coalesce(Partecipa.totale_studenti, 0)),
        func.count(distinct(Partecipa.id_attivita))
    ).group_by(IndirizzoScolastico.indirizzo).order_by(func.sum(Partecipa.totale_studenti).desc()).limit(10).all()

    chart_indirizzi = {
        'labels': [i[0] for i in q_indirizzi],
        'maschi': [i[1] for i in q_indirizzi],
        'femmine': [i[2] for i in q_indirizzi],
        'altro': [(i[3] - (i[1] + i[2])) for i in q_indirizzi],
        'attivita': [i[4] for i in q_indirizzi]
    }

    all_attivita = base_query.order_by(AttivitaOrientamento.data_inizio).all()
    trend_data = defaultdict(lambda: {'studenti': 0, 'm': 0, 'f': 0, 'alt': 0, 'attivita': 0})

    all_ids = [a.id_attivita for a in all_attivita]
    part_data = {}
    if all_ids:
        q_part = session_db.query(
            Partecipa.id_attivita,
            func.sum(Partecipa.totale_studenti),
            func.sum(Partecipa.totale_maschi),
            func.sum(Partecipa.totale_femmine)
        ).filter(Partecipa.id_attivita.in_(all_ids)).group_by(Partecipa.id_attivita).all()

        for row in q_part:
            t, m, f = row[1] or 0, row[2] or 0, row[3] or 0
            part_data[row[0]] = {'tot': t, 'm': m, 'f': f, 'alt': t - (m + f)}

    for att in all_attivita:
        k = att.data_inizio.strftime('%Y-%m')
        trend_data[k]['attivita'] += 1
        if att.id_attivita in part_data:
            d = part_data[att.id_attivita]
            trend_data[k]['studenti'] += d['tot']
            trend_data[k]['m'] += d['m']
            trend_data[k]['f'] += d['f']
            trend_data[k]['alt'] += d['alt']

    sorted_keys = sorted(trend_data.keys())

    chart_trend = {
        'labels': sorted_keys,
        'attivita': [trend_data[k]['attivita'] for k in sorted_keys],
        'studenti': [trend_data[k]['studenti'] for k in sorted_keys],
        'maschi': [trend_data[k]['m'] for k in sorted_keys],
        'femmine': [trend_data[k]['f'] for k in sorted_keys],
        'altro': [trend_data[k]['alt'] for k in sorted_keys]
    }

    q_tot_sesso = partecipazioni_q.with_entities(
        func.sum(func.coalesce(Partecipa.totale_studenti, 0)),
        func.sum(func.coalesce(Partecipa.totale_maschi, 0)),
        func.sum(func.coalesce(Partecipa.totale_femmine, 0))
    ).first()
    ts, tm, tf = q_tot_sesso[0] or 0, q_tot_sesso[1] or 0, q_tot_sesso[2] or 0

    chart_sesso_data = [tm, tf, ts - (tm + tf)]
    chart_sesso_labels = ['Maschi', 'Femmine', 'Altro']

    available_years = session_db.query(distinct(extract('year', AttivitaOrientamento.data_inizio))) \
        .order_by(extract('year', AttivitaOrientamento.data_inizio).desc()).all()
    years_list = [int(y[0]) for y in available_years]

    chart_comp_stud_stacked = {}
    chart_comp_att_line = {}

    if struttura == 'Ateneo di Verona':
        q_comp_stud = session_db.query(
            AttivitaOrientamento.struttura_organizzante,
            func.sum(func.coalesce(Partecipa.totale_maschi, 0)),
            func.sum(func.coalesce(Partecipa.totale_femmine, 0)),
            func.sum(func.coalesce(Partecipa.totale_studenti, 0))
        ).outerjoin(Partecipa).group_by(AttivitaOrientamento.struttura_organizzante).order_by(
            AttivitaOrientamento.struttura_organizzante).all()

        chart_comp_stud_stacked = {
            'labels': [r[0] for r in q_comp_stud],
            'maschi': [r[1] or 0 for r in q_comp_stud],
            'femmine': [r[2] or 0 for r in q_comp_stud],
            'altro': [((r[3] or 0) - ((r[1] or 0) + (r[2] or 0))) for r in q_comp_stud]
        }

        q_comp_att = session_db.query(
            AttivitaOrientamento.struttura_organizzante,
            func.count(AttivitaOrientamento.id_attivita)
        ).group_by(AttivitaOrientamento.struttura_organizzante).order_by(
            AttivitaOrientamento.struttura_organizzante).all()

        chart_comp_att_line = {
            'labels': [r[0] for r in q_comp_att],
            'data': [r[1] for r in q_comp_att]
        }

    return render_template('resoconto.html',
                           struttura=struttura,
                           selected_year=year_filter,
                           years_list=years_list,
                           dip_filter=dip_filter,
                           all_dips=all_dips,
                           kpi={'attivita': kpi_attivita, 'svolte': kpi_svolte, 'programmate': kpi_programmate,
                                'studenti': kpi_studenti, 'scuole': kpi_scuole, 'indirizzi': kpi_indirizzi,
                                'ore': kpi_ore},
                           chart_trend=chart_trend,
                           chart_scuole=chart_scuole,
                           chart_indirizzi=chart_indirizzi,
                           chart_sesso=chart_sesso_data,
                           chart_sesso_labels=chart_sesso_labels,
                           chart_comp_stud_stacked=chart_comp_stud_stacked,
                           chart_comp_att_line=chart_comp_att_line)


@app.route('/api/cerca_attivita')
@login_required
def api_cerca_attivita():
    term = request.args.get('q', '')
    if not term or len(term) < 2: return jsonify([])

    session_db = Database().get_session()
    struttura = session.get('struttura')

    query = session_db.query(AttivitaOrientamento).filter(AttivitaOrientamento.nome.ilike(f"%{term}%"))

    if struttura != 'Ateneo di Verona':
        query = query.outerjoin(Collabora).filter(
            (AttivitaOrientamento.struttura_organizzante == struttura) | (Collabora.nome_struttura == struttura)
        )

    results = query.order_by(AttivitaOrientamento.data_inizio.desc()).limit(20).all()
    return jsonify([{'id': a.id_attivita, 'nome': a.nome, 'data': a.data_inizio.strftime('%d/%m/%Y'),
                     'struttura': a.struttura_organizzante} for a in results])


@app.route('/api/confronta_edizioni')
@login_required
def api_confronta_edizioni():
    ids_param = request.args.get('ids', '')
    if not ids_param: return jsonify({'error': 'Nessun ID specificato'})

    ids = [int(x) for x in ids_param.split(',')]
    session_db = Database().get_session()

    data = session_db.query(
        AttivitaOrientamento.nome,
        AttivitaOrientamento.data_inizio,
        func.sum(Partecipa.totale_studenti),
        func.sum(func.coalesce(Partecipa.totale_maschi, 0)),
        func.sum(func.coalesce(Partecipa.totale_femmine, 0))
    ).join(Partecipa).filter(AttivitaOrientamento.id_attivita.in_(ids)) \
        .group_by(AttivitaOrientamento.id_attivita).all()

    data.sort(key=lambda x: x[1])

    return jsonify({
        'labels': [f"{d[0]} ({d[1].year})" for d in data],
        'values': [d[2] or 0 for d in data],
        'maschi': [d[3] for d in data],
        'femmine': [d[4] for d in data],
        'altro': [(d[2] or 0) - (d[3] + d[4]) for d in data]
    })


if __name__ == "__main__":
    app.run(debug=True)