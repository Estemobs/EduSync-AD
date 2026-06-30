from edusync_ad.core.audit import AuditLog, new_session_id


def test_record_and_query_round_trip(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    session = new_session_id()

    log.record(
        "creation_compte",
        "thomas.martin",
        "succes",
        session,
        ou_destination="OU=3emeA,OU=Eleves,DC=lycee,DC=local",
        simulation=False,
    )
    log.record(
        "creation_compte",
        "lea.dupont",
        "echec",
        session,
        ou_destination="OU=3emeA,OU=Eleves,DC=lycee,DC=local",
        simulation=False,
        detail="Identifiant déjà existant",
    )

    entries = log.query()
    assert len(entries) == 2
    # Tri décroissant (le plus récent en premier)
    assert entries[0].compte == "lea.dupont"
    assert entries[0].resultat == "echec"
    assert entries[1].compte == "thomas.martin"


def test_query_filters_by_action_type_and_resultat(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    session = new_session_id()
    log.record("creation_compte", "a", "succes", session)
    log.record("creation_compte", "b", "echec", session)
    log.record("migration", "c", "succes", session)

    creations = log.query(action_type="creation_compte")
    assert len(creations) == 2

    echecs = log.query(resultat="echec")
    assert len(echecs) == 1
    assert echecs[0].compte == "b"


def test_simulation_flag_is_persisted(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    session = new_session_id()
    log.record("creation_compte", "thomas.martin", "succes", session, simulation=True)

    entries = log.query()
    assert entries[0].simulation is True


def test_export_csv(tmp_path):
    log = AuditLog(tmp_path / "journal.db")
    session = new_session_id()
    log.record("creation_compte", "thomas.martin", "succes", session)

    out_path = tmp_path / "journal.csv"
    log.export_csv(out_path)
    content = out_path.read_text(encoding="utf-8-sig")
    assert "thomas.martin" in content
    assert "action_type" in content


def test_journal_persists_across_reopen(tmp_path):
    db_path = tmp_path / "journal.db"
    log1 = AuditLog(db_path)
    log1.record("creation_compte", "thomas.martin", "succes", new_session_id())
    log1.close()

    log2 = AuditLog(db_path)
    entries = log2.query()
    assert len(entries) == 1
    assert entries[0].compte == "thomas.martin"
