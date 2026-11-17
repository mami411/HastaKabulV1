CREATE TABLE IF NOT EXISTS "__EFMigrationsHistory" (
    migration_id character varying(150) NOT NULL,
    product_version character varying(32) NOT NULL,
    CONSTRAINT pk___ef_migrations_history PRIMARY KEY (migration_id)
);

START TRANSACTION;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE EXTENSION IF NOT EXISTS vector;
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE audit_logs (
        id uuid NOT NULL,
        action character varying(80) NOT NULL,
        actor character varying(80) NOT NULL,
        entity_type character varying(80) NOT NULL,
        entity_id uuid,
        request_id character varying(80) NOT NULL,
        detail character varying(400),
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_audit_logs PRIMARY KEY (id)
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE clinics (
        id uuid NOT NULL,
        code character varying(40) NOT NULL,
        name character varying(120) NOT NULL,
        synonyms text[] NOT NULL,
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_clinics PRIMARY KEY (id)
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE kb_entries (
        id uuid NOT NULL,
        term character varying(160) NOT NULL,
        title character varying(240) NOT NULL,
        body text NOT NULL,
        kind character varying(20) NOT NULL,
        synonyms text[] NOT NULL,
        embedding vector(1536),
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_kb_entries PRIMARY KEY (id)
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE patients (
        id uuid NOT NULL,
        patient_ref uuid NOT NULL,
        tc_hash character varying(64) NOT NULL,
        tc_encrypted bytea NOT NULL,
        first_name character varying(80) NOT NULL,
        last_name character varying(80) NOT NULL,
        phone_masked character varying(20),
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_patients PRIMARY KEY (id)
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE voice_sessions (
        id uuid NOT NULL,
        status character varying(20) NOT NULL,
        patient_id uuid,
        patient_ref uuid,
        attempts integer NOT NULL,
        expires_at_utc timestamp with time zone NOT NULL,
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_voice_sessions PRIMARY KEY (id)
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE doctors (
        id uuid NOT NULL,
        clinic_id uuid NOT NULL,
        full_name character varying(160) NOT NULL,
        room character varying(20) NOT NULL,
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_doctors PRIMARY KEY (id),
        CONSTRAINT fk_doctors_clinics_clinic_id FOREIGN KEY (clinic_id) REFERENCES clinics (id) ON DELETE RESTRICT
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE appointment_slots (
        id uuid NOT NULL,
        clinic_id uuid NOT NULL,
        doctor_id uuid NOT NULL,
        start_time_utc timestamp with time zone NOT NULL,
        duration_minutes integer NOT NULL,
        is_taken boolean NOT NULL,
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_appointment_slots PRIMARY KEY (id),
        CONSTRAINT fk_appointment_slots_doctors_doctor_id FOREIGN KEY (doctor_id) REFERENCES doctors (id) ON DELETE RESTRICT
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE appointments (
        id uuid NOT NULL,
        patient_id uuid NOT NULL,
        doctor_id uuid NOT NULL,
        clinic_id uuid NOT NULL,
        slot_id uuid NOT NULL,
        scheduled_at_utc timestamp with time zone NOT NULL,
        status character varying(20) NOT NULL,
        updated_at_utc timestamp with time zone NOT NULL,
        access_code character varying(6) NOT NULL,
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_appointments PRIMARY KEY (id),
        CONSTRAINT fk_appointments_appointment_slots_slot_id FOREIGN KEY (slot_id) REFERENCES appointment_slots (id) ON DELETE RESTRICT,
        CONSTRAINT fk_appointments_doctors_doctor_id FOREIGN KEY (doctor_id) REFERENCES doctors (id) ON DELETE RESTRICT,
        CONSTRAINT fk_appointments_patients_patient_id FOREIGN KEY (patient_id) REFERENCES patients (id) ON DELETE RESTRICT
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE clinical_entries (
        id uuid NOT NULL,
        appointment_id uuid NOT NULL,
        doctor_id uuid NOT NULL,
        diagnosis character varying(400) NOT NULL,
        medications text[] NOT NULL,
        created_at_utc timestamp with time zone NOT NULL,
        CONSTRAINT pk_clinical_entries PRIMARY KEY (id),
        CONSTRAINT fk_clinical_entries_appointments_appointment_id FOREIGN KEY (appointment_id) REFERENCES appointments (id) ON DELETE CASCADE
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE TABLE simplified_summaries (
        id uuid NOT NULL,
        appointment_id uuid NOT NULL,
        clinical_entry_id uuid NOT NULL,
        patient_ref uuid NOT NULL,
        request_id character varying(80) NOT NULL,
        status character varying(20) NOT NULL,
        updated_at_utc timestamp with time zone NOT NULL,
        diagnosis_plain character varying(2000),
        warnings text[] NOT NULL,
        unknown_terms text[] NOT NULL,
        source_ids text[] NOT NULL,
        failure_code character varying(40),
        approved_by_doctor_id uuid,
        approved_at_utc timestamp with time zone,
        created_at_utc timestamp with time zone NOT NULL,
        medications jsonb,
        CONSTRAINT pk_simplified_summaries PRIMARY KEY (id),
        CONSTRAINT fk_simplified_summaries_appointments_appointment_id FOREIGN KEY (appointment_id) REFERENCES appointments (id) ON DELETE CASCADE
    );
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_appointment_slots_clinic_id_start_time_utc_is_taken ON appointment_slots (clinic_id, start_time_utc, is_taken);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_appointment_slots_doctor_id ON appointment_slots (doctor_id);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE UNIQUE INDEX ix_appointments_access_code ON appointments (access_code);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_appointments_doctor_id_scheduled_at_utc ON appointments (doctor_id, scheduled_at_utc);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_appointments_patient_id_scheduled_at_utc_status ON appointments (patient_id, scheduled_at_utc, status);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE UNIQUE INDEX ix_appointments_slot_id ON appointments (slot_id);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_audit_logs_request_id ON audit_logs (request_id);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_clinical_entries_appointment_id ON clinical_entries (appointment_id);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE UNIQUE INDEX ix_clinics_code ON clinics (code);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_doctors_clinic_id ON doctors (clinic_id);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_kb_entries_term ON kb_entries (term);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE UNIQUE INDEX ix_patients_patient_ref ON patients (patient_ref);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE UNIQUE INDEX ix_patients_tc_hash ON patients (tc_hash);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    CREATE INDEX ix_simplified_summaries_appointment_id ON simplified_summaries (appointment_id);
    END IF;
END $EF$;

DO $EF$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM "__EFMigrationsHistory" WHERE "migration_id" = '20261102211832_InitialSchema') THEN
    INSERT INTO "__EFMigrationsHistory" (migration_id, product_version)
    VALUES ('20261102211832_InitialSchema', '9.0.4');
    END IF;
END $EF$;
COMMIT;

