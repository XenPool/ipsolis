"""Squashed initial schema — replaces migrations 0001-0096.

Revision ID: 0001
Revises: None
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")

    # ── Enum types ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TYPE public.asset_category AS ENUM (
            'application_access',
            'platform_access',
            'data_access',
            'device_access',
            'infrastructure_access'
        )
    """)

    op.execute("""
        CREATE TYPE public.asset_status AS ENUM (
            'Free',
            'reserved',
            'busy',
            'maintenance',
            'Reinstall',
            'Reinstalling',
            'Failed'
        )
    """)

    op.execute("""
        CREATE TYPE public.order_action AS ENUM (
            'provision',
            'modify',
            'extend',
            'delete'
        )
    """)

    op.execute("""
        CREATE TYPE public.order_status AS ENUM (
            'pending',
            'processing',
            'delivered',
            'failed',
            'expired',
            'cancelled',
            'provisioning',
            'provisioned',
            'revoking',
            'revoked',
            'scheduled',
            'pending_approval',
            'rejected'
        )
    """)

    op.execute("""
        CREATE TYPE public.step_status AS ENUM (
            'pending',
            'running',
            'success',
            'failed',
            'skipped'
        )
    """)

    # ── Functions ──────────────────────────────────────────────────────────────
    op.execute("""
        CREATE FUNCTION public.audit_log_no_mutate() RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF current_setting('ipsolis.allow_audit_mutation', true) = 'true' THEN
                    RETURN NULL;
                END IF;
                RAISE EXCEPTION
                    'audit_log is append-only -- % blocked. To bypass for legitimate maintenance, set ipsolis.allow_audit_mutation = ''true'' inside the transaction.',
                    TG_OP;
            END;
            $$
    """)

    # ── Tables ────────────────────────────────────────────────────────────────

    op.execute("""
        CREATE TABLE public.admin_users (
            id integer NOT NULL,
            username character varying(128) NOT NULL,
            password_hash text NOT NULL,
            role character varying(32) NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            last_login_at timestamp with time zone,
            created_by character varying(255) NOT NULL,
            password_set_at timestamp with time zone,
            failed_login_count integer DEFAULT 0 NOT NULL,
            locked_at timestamp with time zone
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.admin_users_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.admin_users_id_seq OWNED BY public.admin_users.id")
    op.execute("ALTER TABLE ONLY public.admin_users ALTER COLUMN id SET DEFAULT nextval('public.admin_users_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.api_tokens (
            id integer NOT NULL,
            name character varying(120) NOT NULL,
            token_hash character varying(64) NOT NULL,
            token_prefix character varying(12) NOT NULL,
            scopes json NOT NULL,
            created_by character varying(255) NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            expires_at timestamp with time zone,
            last_used_at timestamp with time zone,
            revoked_at timestamp with time zone,
            role character varying(32)
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.api_tokens_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.api_tokens_id_seq OWNED BY public.api_tokens.id")
    op.execute("ALTER TABLE ONLY public.api_tokens ALTER COLUMN id SET DEFAULT nextval('public.api_tokens_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.app_config (
            id integer NOT NULL,
            key character varying(255) NOT NULL,
            value text,
            description text,
            is_secret boolean DEFAULT false NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.app_config_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.app_config_id_seq OWNED BY public.app_config.id")
    op.execute("ALTER TABLE ONLY public.app_config ALTER COLUMN id SET DEFAULT nextval('public.app_config_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.approval_delegations (
            id integer NOT NULL,
            approver_email character varying(255) NOT NULL,
            approver_name character varying(255),
            delegate_email character varying(255) NOT NULL,
            delegate_name character varying(255),
            from_at timestamp with time zone NOT NULL,
            until_at timestamp with time zone NOT NULL,
            reason character varying(500),
            created_by character varying(255) NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            revoked_at timestamp with time zone,
            CONSTRAINT ck_delegation_window CHECK ((until_at > from_at))
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.approval_delegations_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.approval_delegations_id_seq OWNED BY public.approval_delegations.id")
    op.execute("ALTER TABLE ONLY public.approval_delegations ALTER COLUMN id SET DEFAULT nextval('public.approval_delegations_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.asset_types (
            id integer NOT NULL,
            name character varying(100) NOT NULL,
            description text,
            config json,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            pool_capacity integer,
            category public.asset_category DEFAULT 'platform_access'::public.asset_category NOT NULL,
            assignment_model character varying(30) DEFAULT 'assigned_personal'::character varying NOT NULL,
            targets jsonb,
            automation_mode character varying(20) DEFAULT 'runbook'::character varying NOT NULL,
            lifecycle_ttl_days integer,
            lifecycle_renewable boolean DEFAULT true NOT NULL,
            deprovision_policy character varying(30) DEFAULT 'access_only'::character varying NOT NULL,
            personal_provisioning_strategy character varying(30),
            naming_pattern character varying(100),
            max_per_user integer DEFAULT 1 NOT NULL,
            automation_strategy character varying(20) DEFAULT 'runbook_only'::character varying NOT NULL,
            composite_steps jsonb,
            allow_rdp_users boolean DEFAULT false NOT NULL,
            allow_admin_users boolean DEFAULT false NOT NULL,
            rds_gateway_url character varying(500),
            requires_manager_approval boolean DEFAULT false NOT NULL,
            requires_owner_approval boolean DEFAULT false NOT NULL,
            approval_owners json,
            requires_approval_on_modify boolean DEFAULT false NOT NULL,
            eligible_requestors_dn character varying(500),
            lifecycle_reminder_days integer,
            logo text,
            is_active boolean DEFAULT true NOT NULL,
            help_text text,
            monthly_cost numeric(12,2),
            currency character varying(3),
            cost_center character varying(100),
            min_approvals_required integer,
            approval_rules json,
            show_on_dashboard boolean DEFAULT false NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.asset_types_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.asset_types_id_seq OWNED BY public.asset_types.id")
    op.execute("ALTER TABLE ONLY public.asset_types ALTER COLUMN id SET DEFAULT nextval('public.asset_types_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.asset_pool (
            id integer NOT NULL,
            name character varying(255) NOT NULL,
            asset_type_id integer NOT NULL,
            status public.asset_status DEFAULT 'Free'::public.asset_status NOT NULL,
            current_order_id integer,
            expires_at timestamp with time zone,
            last_reclaim_at timestamp with time zone,
            metadata json,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.asset_pool_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.asset_pool_id_seq OWNED BY public.asset_pool.id")
    op.execute("ALTER TABLE ONLY public.asset_pool ALTER COLUMN id SET DEFAULT nextval('public.asset_pool_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.orders (
            id integer NOT NULL,
            servicenow_ref character varying(50),
            user_email character varying(255) NOT NULL,
            user_name character varying(255) NOT NULL,
            asset_type_id integer NOT NULL,
            assigned_asset_id integer,
            rdp_users character varying[] DEFAULT '{}'::character varying[] NOT NULL,
            admin_users character varying[] DEFAULT '{}'::character varying[] NOT NULL,
            requested_from timestamp with time zone NOT NULL,
            requested_until timestamp with time zone NOT NULL,
            action public.order_action DEFAULT 'provision'::public.order_action NOT NULL,
            status public.order_status DEFAULT 'pending'::public.order_status NOT NULL,
            celery_task_id character varying(255),
            config json,
            error_message text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            owner_email character varying(255),
            owner_name character varying(255),
            snow_req character varying(50),
            provisioned_state jsonb,
            expiry_reminder_sent_at timestamp with time zone,
            requester_department character varying(255),
            requester_cost_center character varying(100),
            requester_company character varying(255),
            requester_employee_id character varying(50),
            requester_sam_account character varying(100),
            requester_title character varying(255)
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.orders_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.orders_id_seq OWNED BY public.orders.id")
    op.execute("ALTER TABLE ONLY public.orders ALTER COLUMN id SET DEFAULT nextval('public.orders_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.order_approvals (
            id integer NOT NULL,
            order_id integer NOT NULL,
            approver_type character varying(30) NOT NULL,
            approver_email character varying(255) NOT NULL,
            approver_name character varying(255) NOT NULL,
            status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
            decided_at timestamp with time zone,
            comment text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            last_reminded_at timestamp with time zone,
            reminder_count integer DEFAULT 0 NOT NULL,
            escalated_at timestamp with time zone,
            rule_name character varying(200),
            rule_threshold integer,
            sod_exempt boolean DEFAULT false NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.order_approvals_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.order_approvals_id_seq OWNED BY public.order_approvals.id")
    op.execute("ALTER TABLE ONLY public.order_approvals ALTER COLUMN id SET DEFAULT nextval('public.order_approvals_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.order_change_log (
            id integer NOT NULL,
            order_id integer NOT NULL,
            target_type character varying(50) NOT NULL,
            identifier text NOT NULL,
            action character varying(20) NOT NULL,
            principal character varying(255) NOT NULL,
            state character varying(20) DEFAULT 'success'::character varying NOT NULL,
            executed_at timestamp with time zone DEFAULT now() NOT NULL,
            metadata jsonb,
            idempotency_key character varying(255),
            resolved_object_id character varying(255)
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.order_change_log_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.order_change_log_id_seq OWNED BY public.order_change_log.id")
    op.execute("ALTER TABLE ONLY public.order_change_log ALTER COLUMN id SET DEFAULT nextval('public.order_change_log_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.order_steps (
            id integer NOT NULL,
            order_id integer NOT NULL,
            step_name character varying(255) NOT NULL,
            status public.step_status DEFAULT 'pending'::public.step_status NOT NULL,
            started_at timestamp with time zone,
            finished_at timestamp with time zone,
            log_output text,
            error text
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.order_steps_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.order_steps_id_seq OWNED BY public.order_steps.id")
    op.execute("ALTER TABLE ONLY public.order_steps ALTER COLUMN id SET DEFAULT nextval('public.order_steps_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.audit_log (
            id integer NOT NULL,
            entity_type character varying(50) NOT NULL,
            entity_id integer NOT NULL,
            action character varying(100) NOT NULL,
            old_value json,
            new_value json,
            triggered_by character varying(255) NOT NULL,
            context text,
            "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
            classification character varying(20) DEFAULT 'internal'::character varying
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.audit_log_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id")
    op.execute("ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.certification_campaigns (
            id integer NOT NULL,
            name character varying(200) NOT NULL,
            description text,
            scope json NOT NULL,
            due_at timestamp with time zone NOT NULL,
            status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
            started_at timestamp with time zone,
            closed_at timestamp with time zone,
            created_by character varying(255) NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.certification_campaigns_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.certification_campaigns_id_seq OWNED BY public.certification_campaigns.id")
    op.execute("ALTER TABLE ONLY public.certification_campaigns ALTER COLUMN id SET DEFAULT nextval('public.certification_campaigns_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.certification_reviews (
            id integer NOT NULL,
            campaign_id integer NOT NULL,
            order_id integer NOT NULL,
            reviewer_email character varying(255) NOT NULL,
            reviewer_name character varying(255),
            status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
            decided_at timestamp with time zone,
            decided_by character varying(255),
            comment text,
            created_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.certification_reviews_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.certification_reviews_id_seq OWNED BY public.certification_reviews.id")
    op.execute("ALTER TABLE ONLY public.certification_reviews ALTER COLUMN id SET DEFAULT nextval('public.certification_reviews_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.cost_report_snapshots (
            snapshot_date date NOT NULL,
            view character varying(20) NOT NULL,
            dimension_key character varying(255) NOT NULL,
            currency character varying(3) NOT NULL,
            projected_monthly_total numeric(14,2) NOT NULL,
            active_orders integer DEFAULT 0 NOT NULL,
            asset_types integer DEFAULT 0 NOT NULL,
            captured_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE public.cost_thresholds (
            cost_center character varying(100) NOT NULL,
            currency character varying(3) NOT NULL,
            monthly_limit numeric(14,2) NOT NULL,
            recipients text NOT NULL,
            last_alerted_at timestamp with time zone,
            last_alerted_amount numeric(14,2),
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE public.db_backups (
            id integer NOT NULL,
            filename character varying(255) NOT NULL,
            size_bytes bigint,
            status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
            trigger character varying(20) DEFAULT 'manual'::character varying NOT NULL,
            created_by character varying(255),
            note text,
            error text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            finished_at timestamp with time zone
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.db_backups_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.db_backups_id_seq OWNED BY public.db_backups.id")
    op.execute("ALTER TABLE ONLY public.db_backups ALTER COLUMN id SET DEFAULT nextval('public.db_backups_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.email_templates (
            id integer NOT NULL,
            event_key character varying NOT NULL,
            description character varying,
            subject character varying NOT NULL,
            body text NOT NULL,
            available_variables jsonb,
            is_active boolean DEFAULT true NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.email_templates_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.email_templates_id_seq OWNED BY public.email_templates.id")
    op.execute("ALTER TABLE ONLY public.email_templates ALTER COLUMN id SET DEFAULT nextval('public.email_templates_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.global_vars (
            id integer NOT NULL,
            key character varying(100) NOT NULL,
            value text,
            description text,
            is_secret boolean DEFAULT false NOT NULL,
            updated_at timestamp without time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.global_vars_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.global_vars_id_seq OWNED BY public.global_vars.id")
    op.execute("ALTER TABLE ONLY public.global_vars ALTER COLUMN id SET DEFAULT nextval('public.global_vars_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.hr_leaver_events (
            id integer NOT NULL,
            source character varying(20) NOT NULL,
            user_email character varying(255) NOT NULL,
            user_external_id character varying(255),
            raw_payload json,
            status character varying(20) DEFAULT 'received'::character varying NOT NULL,
            error_message text,
            orders_revoked integer DEFAULT 0 NOT NULL,
            approvals_superseded integer DEFAULT 0 NOT NULL,
            reviews_superseded integer DEFAULT 0 NOT NULL,
            received_at timestamp with time zone DEFAULT now() NOT NULL,
            processed_at timestamp with time zone,
            triggered_by character varying(255) NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.hr_leaver_events_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.hr_leaver_events_id_seq OWNED BY public.hr_leaver_events.id")
    op.execute("ALTER TABLE ONLY public.hr_leaver_events ALTER COLUMN id SET DEFAULT nextval('public.hr_leaver_events_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.ps_modules (
            id integer NOT NULL,
            name character varying(100) NOT NULL,
            required_version character varying(50),
            status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
            installed_version character varying(50),
            error_log text,
            created_at timestamp without time zone DEFAULT now() NOT NULL,
            updated_at timestamp without time zone DEFAULT now() NOT NULL,
            source_type character varying(20) DEFAULT 'gallery'::character varying NOT NULL,
            upload_data bytea,
            compatibility character varying(20) DEFAULT 'unknown'::character varying NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.ps_modules_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.ps_modules_id_seq OWNED BY public.ps_modules.id")
    op.execute("ALTER TABLE ONLY public.ps_modules ALTER COLUMN id SET DEFAULT nextval('public.ps_modules_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.script_modules (
            id integer NOT NULL,
            name character varying(100) NOT NULL,
            description text,
            script_content text DEFAULT ''::text NOT NULL,
            script_type character varying(20) DEFAULT 'powershell'::character varying NOT NULL,
            param_schema jsonb,
            is_active boolean DEFAULT true NOT NULL,
            created_at timestamp without time zone DEFAULT now() NOT NULL,
            updated_at timestamp without time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.script_modules_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.script_modules_id_seq OWNED BY public.script_modules.id")
    op.execute("ALTER TABLE ONLY public.script_modules ALTER COLUMN id SET DEFAULT nextval('public.script_modules_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.runbook_definitions (
            id integer NOT NULL,
            name character varying(255) NOT NULL,
            description text,
            asset_type_id integer NOT NULL,
            action public.order_action NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.runbook_definitions_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.runbook_definitions_id_seq OWNED BY public.runbook_definitions.id")
    op.execute("ALTER TABLE ONLY public.runbook_definitions ALTER COLUMN id SET DEFAULT nextval('public.runbook_definitions_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.runbook_steps (
            id integer NOT NULL,
            runbook_id integer NOT NULL,
            "position" integer NOT NULL,
            step_name character varying(255) NOT NULL,
            module_key character varying(255),
            params_template jsonb,
            is_critical boolean DEFAULT true NOT NULL,
            retry_count integer DEFAULT 3 NOT NULL,
            timeout_seconds integer DEFAULT 120 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            script_module_id integer
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.runbook_steps_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.runbook_steps_id_seq OWNED BY public.runbook_steps.id")
    op.execute("ALTER TABLE ONLY public.runbook_steps ALTER COLUMN id SET DEFAULT nextval('public.runbook_steps_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.standalone_runbooks (
            id integer NOT NULL,
            name character varying(255) NOT NULL,
            description text,
            is_active boolean DEFAULT true NOT NULL,
            cron_expression character varying(100),
            cron_enabled boolean DEFAULT false NOT NULL,
            skip_if_running boolean DEFAULT true NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.standalone_runbooks_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.standalone_runbooks_id_seq OWNED BY public.standalone_runbooks.id")
    op.execute("ALTER TABLE ONLY public.standalone_runbooks ALTER COLUMN id SET DEFAULT nextval('public.standalone_runbooks_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.standalone_runbook_steps (
            id integer NOT NULL,
            runbook_id integer NOT NULL,
            "position" integer NOT NULL,
            step_name character varying(255) NOT NULL,
            script_module_id integer,
            params_template json,
            is_critical boolean DEFAULT true NOT NULL,
            retry_count integer DEFAULT 3 NOT NULL,
            timeout_seconds integer DEFAULT 120 NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            always_run boolean DEFAULT false NOT NULL
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.standalone_runbook_steps_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.standalone_runbook_steps_id_seq OWNED BY public.standalone_runbook_steps.id")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_steps ALTER COLUMN id SET DEFAULT nextval('public.standalone_runbook_steps_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.standalone_runbook_runs (
            id integer NOT NULL,
            runbook_id integer NOT NULL,
            trigger character varying(20) NOT NULL,
            triggered_by character varying(255),
            status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
            started_at timestamp with time zone,
            finished_at timestamp with time zone,
            error_message text,
            celery_task_id character varying(255),
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            notes text
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.standalone_runbook_runs_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.standalone_runbook_runs_id_seq OWNED BY public.standalone_runbook_runs.id")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_runs ALTER COLUMN id SET DEFAULT nextval('public.standalone_runbook_runs_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.standalone_runbook_run_steps (
            id integer NOT NULL,
            run_id integer NOT NULL,
            step_name character varying(255) NOT NULL,
            "position" integer DEFAULT 0 NOT NULL,
            status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
            started_at timestamp with time zone,
            finished_at timestamp with time zone,
            log_output text,
            error text
        )
    """)

    op.execute("""
        CREATE SEQUENCE public.standalone_runbook_run_steps_id_seq
            AS integer
            START WITH 1
            INCREMENT BY 1
            NO MINVALUE
            NO MAXVALUE
            CACHE 1
    """)

    op.execute("ALTER SEQUENCE public.standalone_runbook_run_steps_id_seq OWNED BY public.standalone_runbook_run_steps.id")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_run_steps ALTER COLUMN id SET DEFAULT nextval('public.standalone_runbook_run_steps_id_seq'::regclass)")

    op.execute("""
        CREATE TABLE public.admin_user_asset_type_grants (
            admin_user_id integer NOT NULL,
            asset_type_id integer NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            created_by character varying(255) NOT NULL
        )
    """)

    # ── Primary keys & unique constraints ──────────────────────────────────────
    op.execute("ALTER TABLE ONLY public.admin_users ADD CONSTRAINT admin_users_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.admin_users ADD CONSTRAINT admin_users_username_key UNIQUE (username)")
    op.execute("ALTER TABLE ONLY public.api_tokens ADD CONSTRAINT api_tokens_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.api_tokens ADD CONSTRAINT api_tokens_token_hash_key UNIQUE (token_hash)")
    op.execute("ALTER TABLE ONLY public.app_config ADD CONSTRAINT app_config_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.app_config ADD CONSTRAINT app_config_key_key UNIQUE (key)")
    op.execute("ALTER TABLE ONLY public.approval_delegations ADD CONSTRAINT approval_delegations_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.asset_types ADD CONSTRAINT asset_types_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.asset_types ADD CONSTRAINT asset_types_name_key UNIQUE (name)")
    op.execute("ALTER TABLE ONLY public.asset_pool ADD CONSTRAINT asset_pool_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.asset_pool ADD CONSTRAINT asset_pool_name_key UNIQUE (name)")
    op.execute("ALTER TABLE ONLY public.audit_log ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.certification_campaigns ADD CONSTRAINT certification_campaigns_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.certification_reviews ADD CONSTRAINT certification_reviews_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.certification_reviews ADD CONSTRAINT uq_certification_reviews_campaign_order UNIQUE (campaign_id, order_id)")
    op.execute("ALTER TABLE ONLY public.cost_report_snapshots ADD CONSTRAINT cost_report_snapshots_pkey PRIMARY KEY (snapshot_date, view, dimension_key, currency)")
    op.execute("ALTER TABLE ONLY public.cost_thresholds ADD CONSTRAINT cost_thresholds_pkey PRIMARY KEY (cost_center, currency)")
    op.execute("ALTER TABLE ONLY public.db_backups ADD CONSTRAINT db_backups_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.db_backups ADD CONSTRAINT db_backups_filename_key UNIQUE (filename)")
    op.execute("ALTER TABLE ONLY public.email_templates ADD CONSTRAINT email_templates_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.email_templates ADD CONSTRAINT email_templates_event_key_key UNIQUE (event_key)")
    op.execute("ALTER TABLE ONLY public.global_vars ADD CONSTRAINT global_vars_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.global_vars ADD CONSTRAINT global_vars_key_key UNIQUE (key)")
    op.execute("ALTER TABLE ONLY public.hr_leaver_events ADD CONSTRAINT hr_leaver_events_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.order_approvals ADD CONSTRAINT order_approvals_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.order_change_log ADD CONSTRAINT order_change_log_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.order_steps ADD CONSTRAINT order_steps_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.orders ADD CONSTRAINT orders_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.ps_modules ADD CONSTRAINT ps_modules_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.ps_modules ADD CONSTRAINT ps_modules_name_key UNIQUE (name)")
    op.execute("ALTER TABLE ONLY public.runbook_definitions ADD CONSTRAINT runbook_definitions_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.runbook_definitions ADD CONSTRAINT uq_runbook_asset_action UNIQUE (asset_type_id, action)")
    op.execute("ALTER TABLE ONLY public.runbook_steps ADD CONSTRAINT runbook_steps_pkey PRIMARY KEY (id)")
    op.execute('ALTER TABLE ONLY public.runbook_steps ADD CONSTRAINT uq_runbook_step_position UNIQUE (runbook_id, "position")')
    op.execute("ALTER TABLE ONLY public.script_modules ADD CONSTRAINT script_modules_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.script_modules ADD CONSTRAINT script_modules_name_key UNIQUE (name)")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_run_steps ADD CONSTRAINT standalone_runbook_run_steps_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_runs ADD CONSTRAINT standalone_runbook_runs_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_steps ADD CONSTRAINT standalone_runbook_steps_pkey PRIMARY KEY (id)")
    op.execute('ALTER TABLE ONLY public.standalone_runbook_steps ADD CONSTRAINT uq_standalone_step_position UNIQUE (runbook_id, "position")')
    op.execute("ALTER TABLE ONLY public.standalone_runbooks ADD CONSTRAINT standalone_runbooks_pkey PRIMARY KEY (id)")
    op.execute("ALTER TABLE ONLY public.admin_user_asset_type_grants ADD CONSTRAINT admin_user_asset_type_grants_pkey PRIMARY KEY (admin_user_id, asset_type_id)")

    # ── Indexes ────────────────────────────────────────────────────────────────
    op.execute("CREATE UNIQUE INDEX ix_admin_users_username ON public.admin_users USING btree (username)")
    op.execute("CREATE UNIQUE INDEX ix_api_tokens_token_hash ON public.api_tokens USING btree (token_hash)")
    op.execute("CREATE INDEX ix_app_config_key ON public.app_config USING btree (key)")
    op.execute("CREATE INDEX ix_approval_delegations_active ON public.approval_delegations USING btree (approver_email, from_at, until_at)")
    op.execute("CREATE INDEX ix_asset_pool_expires_at ON public.asset_pool USING btree (expires_at)")
    op.execute("CREATE INDEX ix_asset_pool_status ON public.asset_pool USING btree (status)")
    op.execute("CREATE INDEX ix_audit_log_classification ON public.audit_log USING btree (classification)")
    op.execute("CREATE INDEX ix_audit_log_entity_id ON public.audit_log USING btree (entity_id)")
    op.execute("CREATE INDEX ix_audit_log_entity_type ON public.audit_log USING btree (entity_type)")
    op.execute('CREATE INDEX ix_audit_log_timestamp ON public.audit_log USING btree ("timestamp")')
    op.execute("CREATE INDEX ix_certification_reviews_reviewer ON public.certification_reviews USING btree (reviewer_email, status)")
    op.execute("CREATE INDEX ix_certification_reviews_status ON public.certification_reviews USING btree (campaign_id, status)")
    op.execute("CREATE INDEX ix_cost_report_snapshots_view_date ON public.cost_report_snapshots USING btree (view, snapshot_date)")
    op.execute("CREATE INDEX ix_db_backups_created_at ON public.db_backups USING btree (created_at)")
    op.execute("CREATE INDEX ix_hr_leaver_events_email ON public.hr_leaver_events USING btree (user_email)")
    op.execute("CREATE INDEX ix_hr_leaver_events_status_received ON public.hr_leaver_events USING btree (status, received_at)")
    op.execute("CREATE INDEX ix_ocl_idempotency_key ON public.order_change_log USING btree (idempotency_key)")
    op.execute("CREATE INDEX ix_order_approvals_approver_status ON public.order_approvals USING btree (approver_email, status)")
    op.execute("CREATE INDEX ix_order_approvals_order_id ON public.order_approvals USING btree (order_id)")
    op.execute("CREATE INDEX ix_order_change_log_order_id ON public.order_change_log USING btree (order_id)")
    op.execute("CREATE INDEX ix_order_steps_order_id ON public.order_steps USING btree (order_id)")
    op.execute("CREATE INDEX ix_orders_requested_until ON public.orders USING btree (requested_until)")
    op.execute("CREATE INDEX ix_orders_servicenow_ref ON public.orders USING btree (servicenow_ref)")
    op.execute("CREATE INDEX ix_orders_snow_req ON public.orders USING btree (snow_req)")
    op.execute("CREATE INDEX ix_orders_status ON public.orders USING btree (status)")
    op.execute("CREATE INDEX ix_orders_user_email ON public.orders USING btree (user_email)")
    op.execute("CREATE INDEX ix_admin_user_asset_type_grants_type ON public.admin_user_asset_type_grants USING btree (asset_type_id)")

    # ── Foreign keys ──────────────────────────────────────────────────────────
    op.execute("ALTER TABLE ONLY public.admin_user_asset_type_grants ADD CONSTRAINT admin_user_asset_type_grants_admin_user_id_fkey FOREIGN KEY (admin_user_id) REFERENCES public.admin_users(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.admin_user_asset_type_grants ADD CONSTRAINT admin_user_asset_type_grants_asset_type_id_fkey FOREIGN KEY (asset_type_id) REFERENCES public.asset_types(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.asset_pool ADD CONSTRAINT asset_pool_asset_type_id_fkey FOREIGN KEY (asset_type_id) REFERENCES public.asset_types(id)")
    op.execute("ALTER TABLE ONLY public.asset_pool ADD CONSTRAINT asset_pool_current_order_id_fkey FOREIGN KEY (current_order_id) REFERENCES public.orders(id)")
    op.execute("ALTER TABLE ONLY public.certification_reviews ADD CONSTRAINT certification_reviews_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.certification_campaigns(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.certification_reviews ADD CONSTRAINT certification_reviews_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.orders ADD CONSTRAINT fk_orders_assigned_asset_id FOREIGN KEY (assigned_asset_id) REFERENCES public.asset_pool(id)")
    op.execute("ALTER TABLE ONLY public.order_approvals ADD CONSTRAINT order_approvals_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.order_change_log ADD CONSTRAINT order_change_log_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.order_steps ADD CONSTRAINT order_steps_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.orders ADD CONSTRAINT orders_asset_type_id_fkey FOREIGN KEY (asset_type_id) REFERENCES public.asset_types(id)")
    op.execute("ALTER TABLE ONLY public.runbook_definitions ADD CONSTRAINT runbook_definitions_asset_type_id_fkey FOREIGN KEY (asset_type_id) REFERENCES public.asset_types(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.runbook_steps ADD CONSTRAINT runbook_steps_runbook_id_fkey FOREIGN KEY (runbook_id) REFERENCES public.runbook_definitions(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.runbook_steps ADD CONSTRAINT runbook_steps_script_module_id_fkey FOREIGN KEY (script_module_id) REFERENCES public.script_modules(id) ON DELETE SET NULL")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_run_steps ADD CONSTRAINT standalone_runbook_run_steps_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.standalone_runbook_runs(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_runs ADD CONSTRAINT standalone_runbook_runs_runbook_id_fkey FOREIGN KEY (runbook_id) REFERENCES public.standalone_runbooks(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_steps ADD CONSTRAINT standalone_runbook_steps_runbook_id_fkey FOREIGN KEY (runbook_id) REFERENCES public.standalone_runbooks(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ONLY public.standalone_runbook_steps ADD CONSTRAINT standalone_runbook_steps_script_module_id_fkey FOREIGN KEY (script_module_id) REFERENCES public.script_modules(id) ON DELETE SET NULL")

    # ── Triggers (audit_log append-only) ─────────────────────────────────────
    op.execute("CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON public.audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.audit_log_no_mutate()")
    op.execute("CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON public.audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.audit_log_no_mutate()")
    op.execute("CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON public.audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.audit_log_no_mutate()")

    # ── Seed data: app_config ─────────────────────────────────────────────────
    # Core email/AD/company defaults (from 0003)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('email.smtp_server',  'localhost',           'SMTP-Server Hostname',                 false, NOW(), NOW()),
        ('email.smtp_port',    '25',                  'SMTP-Port (25=plain, 587=STARTTLS)',    false, NOW(), NOW()),
        ('email.from',         'noreply@example.com', 'Absender-Adresse',                     false, NOW(), NOW()),
        ('email.bcc',          'it@example.com',      'BCC-Empfanger fur alle Systemmails',   false, NOW(), NOW()),
        ('email.username',     '',                    'SMTP-Benutzername (leer = kein Auth)', false, NOW(), NOW()),
        ('email.password',     '',                    'SMTP-Passwort',                        true,  NOW(), NOW()),
        ('ad.server',          'dc.example.com',      'LDAP-Server (Domain Controller)',      false, NOW(), NOW()),
        ('ad.port',            '389',                 'LDAP-Port (389=plain, 636=SSL)',        false, NOW(), NOW()),
        ('ad.base_dn',         'DC=example,DC=com',   'LDAP Base DN fur Benutzersuche',       false, NOW(), NOW()),
        ('ad.domain',          'EXAMPLE',             'NetBIOS-Domainname fur Bind',          false, NOW(), NOW()),
        ('ad.username',        'svc_vdi',             'Service-Account fur LDAP-Bind',        false, NOW(), NOW()),
        ('ad.password',        '',                    'Passwort des Service-Accounts',        true,  NOW(), NOW()),
        ('company.name',       'XenPool',             'Firmenname fur E-Mail-Templates',      false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # email.from_name (from 0016) — final rebranded value
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES ('email.from_name', 'Ipsolis', 'Display name shown in the From field of outgoing emails', false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # vSphere / XenServer hosting config (from 0017)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('vsphere.host',       '', 'vCenter / ESXi hostname or IP',     false, NOW(), NOW()),
        ('vsphere.username',   '', 'vSphere admin service account',      false, NOW(), NOW()),
        ('vsphere.password',   '', 'vSphere admin password',             true,  NOW(), NOW()),
        ('xenserver.host',     '', 'XCP-ng / XenServer hostname or IP', false, NOW(), NOW()),
        ('xenserver.username', '', 'XenServer admin service account',    false, NOW(), NOW()),
        ('xenserver.password', '', 'XenServer admin password',           true,  NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Entra ID / Azure AD SSO (from 0019)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('entra.mode',            'disabled', 'Portal SSO mode: disabled | entra_only | entra_with_onprem', false, NOW(), NOW()),
        ('entra.tenant_id',       '',         'Azure Tenant ID (GUID)',                                     false, NOW(), NOW()),
        ('entra.client_id',       '',         'App Registration Client ID (GUID)',                          false, NOW(), NOW()),
        ('entra.client_secret',   '',         'App Registration Client Secret',                             true,  NOW(), NOW()),
        ('entra.redirect_uri',    '',         'OAuth2 callback URL (must match App Registration)',          false, NOW(), NOW()),
        ('entra.allowed_domains', '',         'Comma-separated UPN suffixes allowed to log in (blank = any)', false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # App branding (from 0022-0025) — use final rebranded value
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('app.title',           'Ipsolis', 'Application title shown in the navigation bar and browser tab', false, NOW(), NOW()),
        ('app.logo',            '',        'Portal logo stored as base64 data URL (SVG/PNG/JPG, max 1 MB)', false, NOW(), NOW()),
        ('app.logo_position',   'left',    'Logo alignment in the portal sidebar: left | center | right',   false, NOW(), NOW()),
        ('app.logo_size',       '80',      'Logo width as a percentage of the sidebar width (20-100)',       false, NOW(), NOW()),
        ('app.logo_show_title', 'true',    'Show the application title below the portal logo (true | false)', false, NOW(), NOW()),
        ('app.logo_title_size', '12',      'Font size (px) of the application title shown below the portal logo (8-24)', false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Backup + health alerts (from 0042)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('backup.enabled',                'false',     'Enable scheduled database backups via Celery Beat.',                    false, NOW(), NOW()),
        ('backup.schedule_cron',          '0 2 * * *', 'Cron expression (UTC) for scheduled backups. Default: daily 02:00.',   false, NOW(), NOW()),
        ('health.alert_enabled',          'false',     'Send an email when a health probe flips to FAILED (or back to OK).',   false, NOW(), NOW()),
        ('health.alert_email',            '',          'Recipient email address for Maintenance health alerts.',               false, NOW(), NOW()),
        ('health.alert_cooldown_minutes', '60',        'Suppress repeat failure alerts for this many minutes per service.',    false, NOW(), NOW()),
        ('health.last_state',             '{}',        'Internal: JSON snapshot of last health probe result per service.',     false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # SCCM Admin Service config (from 0038)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('sccm.base_url',   '', 'SCCM Admin Service base URL, e.g. https://sccm.example.com/AdminService', false, NOW(), NOW()),
        ('sccm.username',   '', 'SCCM service account (DOMAIN\\user)',                                      false, NOW(), NOW()),
        ('sccm.password',   '', 'SCCM service account password',                                            true,  NOW(), NOW()),
        ('sccm.verify_tls', 'true', 'Verify TLS certificate (true/false)',                                  false, NOW(), NOW()),
        ('sccm.site_code',  '', 'Primary site code, e.g. P01',                                              false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Microsoft Teams notifications (from 0051)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('teams.mode',        'disabled', 'Microsoft Teams approval notifications: disabled or enabled.',                       false, NOW(), NOW()),
        ('teams.webhook_url', '',         'Teams Workflows webhook URL.',                                                       true,  NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Prometheus metrics (from 0052)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES ('metrics.enabled', 'true', 'Expose the Prometheus /metrics endpoint. Set to false to return 404.', false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # SIEM config (from 0053, description updated by 0065/0068/0090)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('siem.enabled',      'false',      'SIEM audit-log streaming master switch (true/false).',                                                          false, NOW(), NOW()),
        ('siem.format',       'splunk_hec', 'SIEM payload format. One of: splunk_hec, sentinel (legacy Data Collector API), sentinel_log_ingestion, or webhook.', false, NOW(), NOW()),
        ('siem.endpoint_url', '',           'SIEM ingestion endpoint, e.g. https://splunk:8088/services/collector/event',                                   false, NOW(), NOW()),
        ('siem.token',        '',           'Splunk HEC token.',                                                                                             true,  NOW(), NOW()),
        ('siem.batch_size',   '200',        'Maximum audit_log rows forwarded per Beat tick.',                                                               false, NOW(), NOW()),
        ('siem.verify_tls',   'true',       'Verify SIEM endpoint TLS certificate.',                                                                        false, NOW(), NOW()),
        ('siem.last_id',      '0',          'Auto-managed cursor -- last audit_log id successfully forwarded.',                                              false, NOW(), NOW()),
        ('siem.last_error',   '',           'Auto-managed -- most recent streaming failure (empty on success).',                                             false, NOW(), NOW()),
        ('siem.last_success_at', '',        'Auto-managed -- ISO timestamp of the last successful batch.',                                                   false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Sentinel SIEM keys (from 0065)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('siem.workspace_id', '',             'Sentinel: Log Analytics workspace GUID.',                                                    false, NOW(), NOW()),
        ('siem.shared_key',   '',             'Sentinel: workspace shared key, base64-encoded.',                                            true,  NOW(), NOW()),
        ('siem.log_type',     'IpsolisAudit', 'Sentinel: custom log table name (the _CL suffix is appended automatically).',               false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Webhook SIEM keys (from 0068)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('siem.webhook_url',              '',                    'Webhook: HTTPS endpoint that accepts a signed JSON array of audit events.', false, NOW(), NOW()),
        ('siem.webhook_secret',           '',                    'Webhook: shared secret for HMAC-SHA256 signing.',                          true,  NOW(), NOW()),
        ('siem.webhook_signature_header', 'X-Hub-Signature-256', 'Webhook: header name for the sha256=<hex> signature.',                    false, NOW(), NOW()),
        ('siem.webhook_extra_headers',    '',                    'Webhook: optional additional headers as a JSON object.',                   false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Approval reminders (from 0055)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('approval.reminders_enabled',   'true', 'Send reminder notifications to approvers who have not yet decided.', false, NOW(), NOW()),
        ('approval.reminder_after_hours','24',   'Hours since the last notification before a reminder is sent.',        false, NOW(), NOW()),
        ('approval.max_reminders',       '3',    'Maximum number of reminders sent per pending approval.',              false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Approval escalation (from 0059)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES ('approval.escalation_email', '', 'Comma-separated email(s) notified when an approval has burned through its reminders without a decision. Empty = escalation disabled.', false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # OpenTelemetry tracing (from 0060)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('otel.enabled',          'false',       'Master switch for OpenTelemetry tracing. Restart the API after toggling.', false, NOW(), NOW()),
        ('otel.service_name',     'ipsolis-api', 'Service name written into every span resource attributes.',                false, NOW(), NOW()),
        ('otel.endpoint',         '',            'OTLP HTTP collector endpoint. Empty disables OTLP export.',                false, NOW(), NOW()),
        ('otel.headers',          '',            'Optional headers for the OTLP exporter (one key=value per line).',         true,  NOW(), NOW()),
        ('otel.console_exporter', 'false',       'Print spans to stdout for local verification.',                           false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Audit-log retention (from 0063)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('retention.audit_log_days', '0', 'Audit log retention window in days. 0 disables pruning.', false, NOW(), NOW()),
        ('retention.last_run_at',    '',  'Auto-managed -- ISO timestamp of the last successful retention run.', false, NOW(), NOW()),
        ('retention.last_pruned',    '0', 'Auto-managed -- number of rows deleted in the last retention run.',   false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # External secret backend (from 0072, descriptions final from 0086/0088/0089)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('secret.backend',            'db',     'External secret backend: db (default), vault, ccp, azurekv, awssm, or conjur.', false, NOW(), NOW()),
        ('secret.cache_ttl_seconds',  '60',     'Resolved-secret cache TTL in seconds.',                                         false, NOW(), NOW()),
        ('secret.vault.url',          '',        'Vault: base URL, e.g. https://vault.example.com:8200',                         false, NOW(), NOW()),
        ('secret.vault.token',        '',        'Vault static token. Used only when secret.vault.auth_method = token.',          true,  NOW(), NOW()),
        ('secret.vault.namespace',    '',        'Vault: optional Enterprise namespace. Sent as X-Vault-Namespace.',              false, NOW(), NOW()),
        ('secret.vault.kv_mount',     'secret',  'Vault: KV v2 mount point for vault:// references.',                           false, NOW(), NOW()),
        ('secret.vault.auth_method',  'token',   'Vault authentication method: token, approle, or kubernetes.',                  false, NOW(), NOW()),
        ('secret.vault.approle_path', 'approle', 'AppRole mount path.',                                                          false, NOW(), NOW()),
        ('secret.vault.approle_role_id',   '',   'AppRole role_id.',                                                             false, NOW(), NOW()),
        ('secret.vault.approle_secret_id', '',   'AppRole secret_id.',                                                           true,  NOW(), NOW()),
        ('secret.vault.k8s_path',     'kubernetes', 'Kubernetes auth mount path.',                                               false, NOW(), NOW()),
        ('secret.vault.k8s_role',     '',           'Kubernetes auth role name.',                                                false, NOW(), NOW()),
        ('secret.vault.k8s_jwt_path', '/var/run/secrets/kubernetes.io/serviceaccount/token', 'Path to the projected service-account JWT.', false, NOW(), NOW()),
        ('secret.ccp.url',            '',        'CCP: base URL of the AAM Web Service.',                                        false, NOW(), NOW()),
        ('secret.ccp.app_id',         '',        'CCP: AppID configured for ipSolis.',                                           false, NOW(), NOW()),
        ('secret.ccp.safe',           '',        'CCP: default Safe.',                                                           false, NOW(), NOW()),
        ('secret.ccp.client_cert_pem','',        'CCP: optional client certificate (PEM).',                                      true,  NOW(), NOW()),
        ('secret.ccp.verify_tls',     'true',    'CCP: verify the server TLS certificate.',                                      false, NOW(), NOW()),
        ('secret.last_test_at',       '',        'Auto-managed -- ISO timestamp of the last successful backend connection test.', false, NOW(), NOW()),
        ('secret.last_test_error',    '',        'Auto-managed -- last test-failure message.',                                    false, NOW(), NOW()),
        ('secret.azurekv.tenant_id',        '', 'Azure KV: Azure AD tenant id (GUID).',  false, NOW(), NOW()),
        ('secret.azurekv.client_id',        '', 'Azure KV: Application (client) id.',    false, NOW(), NOW()),
        ('secret.azurekv.client_secret',    '', 'Azure KV: Client secret.',              true,  NOW(), NOW()),
        ('secret.azurekv.api_version',      '7.4', 'Azure KV: REST API version.',        false, NOW(), NOW()),
        ('secret.azurekv.migration_vault',  '', 'Migration tool: Azure Key Vault name.', false, NOW(), NOW()),
        ('secret.awssm.region',              'us-east-1', 'AWS SM: region.',             false, NOW(), NOW()),
        ('secret.awssm.access_key_id',       '',          'AWS SM: IAM access key id.',  false, NOW(), NOW()),
        ('secret.awssm.secret_access_key',   '',          'AWS SM: IAM secret access key.', true, NOW(), NOW()),
        ('secret.awssm.session_token',       '',          'AWS SM: optional STS session token.', true, NOW(), NOW()),
        ('secret.awssm.auth_method',         'static',    'AWS SM: authentication method: static or assume_role.', false, NOW(), NOW()),
        ('secret.awssm.role_arn',            '',          'AWS SM: AssumeRole target ARN.',       false, NOW(), NOW()),
        ('secret.awssm.role_session_name',   'ipsolis',   'AWS SM: AssumeRole session name.',     false, NOW(), NOW()),
        ('secret.awssm.role_external_id',    '',          'AWS SM: AssumeRole optional external_id.', false, NOW(), NOW()),
        ('secret.awssm.role_duration_seconds','3600',     'AWS SM: Requested AssumeRole session length in seconds.', false, NOW(), NOW()),
        ('secret.conjur.url',        '', 'Conjur: API base URL.',    false, NOW(), NOW()),
        ('secret.conjur.account',    '', 'Conjur: account name.',    false, NOW(), NOW()),
        ('secret.conjur.host_id',    '', 'Conjur: host identity.',   false, NOW(), NOW()),
        ('secret.conjur.api_key',    '', 'Conjur: API key.',         true,  NOW(), NOW()),
        ('secret.conjur.verify_tls', 'true', 'Conjur: verify TLS.', false, NOW(), NOW()),
        ('secret.migration_prefix',  'ipsolis', 'Migration tool: name prefix for pushed keys.', false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Update notifier (from 0074 + 0075)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('updates.check_enabled',      'false', 'Update notifier master toggle. OFF by default for air-gapped tenants.', false, NOW(), NOW()),
        ('updates.repo_url',           'https://api.github.com/repos/XenPool/ipsolis', 'GitHub API URL for the update notifier.', false, NOW(), NOW()),
        ('updates.latest_version',     '', 'Latest release tag observed by the update notifier.',   false, NOW(), NOW()),
        ('updates.latest_url',         '', 'HTML URL of the latest release.',                       false, NOW(), NOW()),
        ('updates.latest_published_at','', 'ISO-8601 timestamp of the latest release publication.', false, NOW(), NOW()),
        ('updates.checked_at',         '', 'ISO-8601 timestamp of the last successful update check.',  false, NOW(), NOW()),
        ('updates.check_error',        '', 'Last error from the update notifier, empty on success.', false, NOW(), NOW()),
        ('updates.github_token',       '', 'Optional GitHub PAT for private repos.',                true,  NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Sentinel Logs Ingestion API (from 0090)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('siem.sentinel_dce_endpoint',     '', 'Sentinel Logs Ingestion API: Data Collection Endpoint URL.',    false, NOW(), NOW()),
        ('siem.sentinel_dcr_immutable_id', '', 'Sentinel Logs Ingestion API: Data Collection Rule immutable id.', false, NOW(), NOW()),
        ('siem.sentinel_stream_name',      'Custom-IpsolisAudit_CL', 'Sentinel Logs Ingestion API: stream name declared on the DCR.', false, NOW(), NOW()),
        ('siem.sentinel_tenant_id',        '', 'Sentinel Logs Ingestion API: Azure AD tenant id (GUID).',       false, NOW(), NOW()),
        ('siem.sentinel_client_id',        '', 'Sentinel Logs Ingestion API: Application (client) id.',         false, NOW(), NOW()),
        ('siem.sentinel_client_secret',    '', 'Sentinel Logs Ingestion API: client secret.',                   true,  NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Auto-decline (from 0078)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('approval.auto_decline_enabled',    'false', 'Master switch for auto-declining stale pending approvals.', false, NOW(), NOW()),
        ('approval.auto_decline_after_days', '0',     'Days a pending approval may sit before the system declines it. 0 = disabled.', false, NOW(), NOW()),
        ('approval.auto_decline_message',    'Auto-declined: no decision recorded within the configured inactivity window. Re-submit the request if access is still required.', 'Decline reason recorded on the approval row.', false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Per-classification approval routing (from 0091)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at) VALUES
        ('approval.classification_policy.pii', 'none', 'Approval policy for PII-bearing orders: none or compliance_officer.', false, NOW(), NOW()),
        ('approval.classification_policy.phi', 'none', 'Approval policy for PHI-bearing orders: none or compliance_officer.', false, NOW(), NOW()),
        ('approval.classification_policy.pci', 'none', 'Approval policy for PCI-bearing orders: none or compliance_officer.', false, NOW(), NOW()),
        ('approval.compliance_officer_email',  '', 'Email receiving compliance-officer approval requests.',                    false, NOW(), NOW()),
        ('approval.compliance_officer_name',   'Compliance Officer', 'Display name for the compliance officer.',              false, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)

    # Escalation assignment mode (from 0092)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES (
            'approval.escalation_assign', 'false',
            'Escalation behaviour. false = notify-only. true = create new approval rows for escalation contacts.',
            false, NOW(), NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)

    # API token purge (from 0093)
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES (
            'api_tokens.purge_after_days', '0',
            'Hard-delete API token rows whose revoked_at OR expires_at is older than this many days. 0 = disabled.',
            false, NOW(), NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)

    # Install UUID (from 0094) -- generated via pgcrypto
    op.execute("""
        INSERT INTO app_config (key, value, description, is_secret, created_at, updated_at)
        VALUES (
            'install.uuid',
            gen_random_uuid()::text,
            'Stable per-install identifier. Used by the license verifier to bind Enterprise licenses to a single deployment.',
            false, NOW(), NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """)

    # ── Seed data: email_templates ────────────────────────────────────────────
    # Core templates (from 0016, post-0043 rebrand, post-0044 app_title substitution)
    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active) VALUES
        (
            'order_confirmation',
            'Sent to requester (and owner if different) when an order is submitted',
            '[{{app_title}}] Order confirmed - {{asset_type_name}}',
            '<p>Hello {{requester_name}},</p>
<p>your order has been successfully submitted and is now being processed.</p>
<table style="font-size:13px;border-collapse:collapse;">
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Type:</td><td style="padding:4px 0;font-weight:bold;">{{asset_type_name}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Description:</td><td style="padding:4px 0;">{{asset_type_description}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Period:</td><td style="padding:4px 0;">{{from_date}} - {{until_date}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Requestor:</td><td style="padding:4px 0;">{{requester_name}} &lt;{{requester_email}}&gt;</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Owner:</td><td style="padding:4px 0;">{{owner_name}} &lt;{{owner_email}}&gt;</td></tr>
</table>
<p style="font-size:12px;color:#888;margin-top:16px;">You will receive another notification once the resource has been provisioned.</p>',
            '[{"name":"company_name"},{"name":"requester_name"},{"name":"requester_email"},{"name":"owner_name"},{"name":"owner_email"},{"name":"asset_type_name"},{"name":"asset_type_description"},{"name":"from_date"},{"name":"until_date"},{"name":"snow_req"},{"name":"snow_ritm"}]',
            true
        ),
        (
            'provision_confirmation',
            'Sent when the resource has been fully provisioned and is ready to use',
            '[{{app_title}}] Your access {{asset_name}} is ready',
            '<p>Hello {{requester_name}},</p>
<p>your resource has been successfully provisioned and is ready to use.</p>
<table style="font-size:13px;border-collapse:collapse;">
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Name:</td><td style="padding:4px 0;font-weight:bold;">{{asset_name}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">RDP Users:</td><td style="padding:4px 0;">{{rdp_users}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Available until:</td><td style="padding:4px 0;">{{expires_at}}</td></tr>
</table>
<p>Please connect to <strong>{{asset_name}}</strong> using Remote Desktop.</p>',
            '[{"name":"company_name"},{"name":"requester_name"},{"name":"requester_email"},{"name":"asset_name"},{"name":"rdp_users"},{"name":"expires_at"}]',
            true
        ),
        (
            'expiry_reminder',
            'Sent when a resource is about to expire (configurable hours before expiry)',
            'Reminder: Your access {{asset_name}} expires in {{hours_remaining}}h',
            '<p>Hello {{requester_name}},</p>
<p>your resource is expiring soon.</p>
<table style="font-size:13px;border-collapse:collapse;">
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Name:</td><td style="padding:4px 0;font-weight:bold;">{{asset_name}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Expires at:</td><td style="padding:4px 0;">{{expires_at}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Remaining:</td><td style="padding:4px 0;">approx. {{hours_remaining}} hours</td></tr>
</table>
<p>If you need it longer, please extend the duration in the Ipsolis portal before the expiry date.</p>',
            '[{"name":"company_name"},{"name":"requester_name"},{"name":"asset_name"},{"name":"expires_at"},{"name":"hours_remaining"}]',
            true
        ),
        (
            'reclaim_notification',
            'Sent when a resource is returned to the pool (after cancellation or expiry)',
            'Your access {{asset_name}} has been returned',
            '<p>Hello {{requester_name}},</p>
<p>your resource <strong>{{asset_name}}</strong> has been returned to the pool and is being reset.</p>
<p>If you need a new resource, feel free to place a new order in the Ipsolis portal.</p>',
            '[{"name":"company_name"},{"name":"requester_name"},{"name":"asset_name"}]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)

    # modify_confirmation (from 0020)
    op.execute("""
        INSERT INTO email_templates (event_key, subject, body, is_active) VALUES
        (
            'modify_confirmation',
            '[{{app_title}}] Your access {{asset_name}} has been updated',
            '<p>Hello {{requester_name}},</p>
<p>your access has been updated and is ready to use.</p>
<table style="font-size:13px;border-collapse:collapse;">
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Name:</td><td style="padding:4px 0;font-weight:bold;">{{asset_name}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">RDP Users:</td><td style="padding:4px 0;">{{rdp_users}}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#555;">Valid until:</td><td style="padding:4px 0;">{{expires_at}}</td></tr>
</table>
<p style="margin-top:16px;">The RDP file is attached - open it to connect directly to your virtual machine.</p>',
            TRUE
        )
        ON CONFLICT (event_key) DO NOTHING
    """)

    # Approval templates (from 0028)
    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active) VALUES
        (
            'approval_request',
            'Sent to each approver when an order requires their approval',
            '[{{app_title}}] Approval required - {{asset_type_name}}',
            '<p>Hello {{approver_name}},</p>
<p><strong>{{requester_name}}</strong> ({{requester_email}}) has requested access to <strong>{{asset_type_name}}</strong> and requires your approval.</p>
<p><strong>Requested period:</strong> {{from_date}} - {{until_date}}</p>
<p>Please review and approve or decline this request in the Self-Service Portal:</p>
<p><a href="{{approval_url}}" style="color:#BB0A30;font-weight:bold;">Review Request</a></p>',
            '["company_name","approver_name","requester_name","requester_email","asset_type_name","from_date","until_date","approval_url"]',
            true
        ),
        (
            'approval_granted',
            'Sent to the requester when all approvals are granted',
            '[{{app_title}}] Your order has been approved - {{asset_type_name}}',
            '<p>Hello {{requester_name}},</p>
<p>Your request for <strong>{{asset_type_name}}</strong> has been approved by all required approvers.</p>
<p>Your order is now being processed and you will receive a confirmation once provisioning is complete.</p>',
            '["company_name","requester_name","requester_email","asset_type_name"]',
            true
        ),
        (
            'approval_declined',
            'Sent to the requester when an approver declines',
            '[{{app_title}}] Your order was declined - {{asset_type_name}}',
            '<p>Hello {{requester_name}},</p>
<p>Your request for <strong>{{asset_type_name}}</strong> has been declined by <strong>{{approver_name}}</strong>.</p>
{{decline_reason_block}}
<p>If you believe this was a mistake, please contact your manager or the application owner directly.</p>',
            '["company_name","requester_name","requester_email","asset_type_name","approver_name","decline_reason_block"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)

    # Escalation template (from 0059)
    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active) VALUES
        (
            'approval_escalated',
            'Sent to the configured escalation contact(s) when an approval has run out of reminders without a decision.',
            '[{{app_title}}] Approval overdue - {{asset_type_name}}',
            '<p>Hello,</p>
<p>An approval request has not been acted on after {{reminder_count}} reminders and is now being escalated.</p>
<p><strong>Original approver:</strong> {{approver_name}} &lt;{{approver_email}}&gt;<br>
<strong>Requester:</strong> {{requester_name}} &lt;{{requester_email}}&gt;<br>
<strong>Asset:</strong> {{asset_type_name}}<br>
<strong>Requested period:</strong> {{from_date}} - {{until_date}}</p>
<p>Please intervene - chase the original approver, reassign the request, or cancel the order via the admin UI.</p>
<p><a href="{{approval_url}}" style="color:#BB0A30;font-weight:bold;">Open in {{app_title}}</a></p>',
            '["company_name","app_title","approver_name","approver_email","requester_name","requester_email","asset_type_name","from_date","until_date","approval_url","reminder_count"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)

    # Escalation-as-assignment template (from 0092)
    op.execute("""
        INSERT INTO email_templates (event_key, description, subject, body, available_variables, is_active) VALUES
        (
            'approval_escalation_assigned',
            'Sent to approval.escalation_email contacts when an approval is escalated AND approval.escalation_assign=true.',
            '[{{app_title}}] Approval reassigned to you - {{asset_type_name}}',
            '<p>Hello,</p>
<p>An approval request has been reassigned to you after the original approver missed the response window.</p>
<p><strong>Original approver:</strong> {{approver_name}} &lt;{{approver_email}}&gt;<br>
<strong>Requester:</strong> {{requester_name}} &lt;{{requester_email}}&gt;<br>
<strong>Asset:</strong> {{asset_type_name}}<br>
<strong>Requested period:</strong> {{from_date}} - {{until_date}}</p>
<p>You can decide directly from this email:</p>
<p><a href="{{approval_url}}" style="background:#BB0A30;color:#fff;padding:8px 14px;text-decoration:none;border-radius:4px;font-weight:bold;">Review and decide</a></p>
<p style="font-size:12px;color:#666;">The original approval request stays in the order history; this is a new step assigned to you.</p>',
            '["company_name","app_title","approver_name","approver_email","requester_name","requester_email","asset_type_name","from_date","until_date","approval_url"]',
            true
        )
        ON CONFLICT (event_key) DO NOTHING
    """)

    # ── Seed data: script_modules (SCCM, from 0039) ───────────────────────────
    from sqlalchemy import text as sa_text
    conn = op.get_bind()

    conn.execute(sa_text("""
        INSERT INTO script_modules (name, description, script_content, script_type, param_schema, is_active)
        VALUES
          (:n1, :d1, :s1, 'powershell', CAST(:p1 AS jsonb), true),
          (:n2, :d2, :s2, 'powershell', CAST(:p2 AS jsonb), true),
          (:n3, :d3, :s3, 'powershell', CAST(:p3 AS jsonb), true)
        ON CONFLICT (name) DO NOTHING
    """), {
        "n1": "SCCM - Delete Device",
        "d1": "Deletes a device from SCCM via the Admin Service (NTLM). Aborts if the name resolves to multiple devices.",
        "s1": (
            "param(\n"
            "    [Parameter(Mandatory=$true)][string]$VMName\n"
            ")\n\n"
            "if ([string]::IsNullOrWhiteSpace($VMName)) {\n"
            "    Write-Output (@{ success = $false; error = 'VMName is empty' } | ConvertTo-Json -Compress)\n"
            "    exit 1\n"
            "}\n\n"
            "$json = python /app/tasks/utils/sccm_admin.py delete-device --name \"$VMName\"\n"
            "$exit = $LASTEXITCODE\n\n"
            "Write-Output $json\n"
            "if ($exit -ne 0) { exit $exit }\n\n"
            "try {\n"
            "    $parsed = $json | ConvertFrom-Json\n"
            "    $global:SCCMDeleteResourceID = $parsed.resource_id\n"
            "    $global:SCCMDeleteCount      = $parsed.deleted\n"
            "} catch { }\n"
        ),
        "p1": '[{"name":"VMName","type":"string","required":true}]',
        "n2": "SCCM - Import Device and Assign Collections",
        "d2": "Imports a device into SCCM (MAC+GUID) via the Admin Service, adds it to the OS deployment collection and any optional app collections, then triggers refreshes.",
        "s2": (
            "param(\n"
            "    [Parameter(Mandatory=$true)][string]$VMName,\n"
            "    [Parameter(Mandatory=$true)][string]$OSCollectionID,\n"
            "    [Parameter(Mandatory=$true)][string]$MACAddress,\n"
            "    [Parameter(Mandatory=$true)][string]$SCCMGuiD,\n"
            "    [string]$AppCollectionIDs = \"\",\n"
            "    [int]$ResourceIdRetries = 60\n"
            ")\n\n"
            "$args = @(\n"
            "    \"import-machine\",\n"
            "    \"--name\",                $VMName,\n"
            "    \"--os-collection\",       $OSCollectionID,\n"
            "    \"--mac\",                 $MACAddress,\n"
            "    \"--guid\",                $SCCMGuiD,\n"
            "    \"--resource-id-retries\", \"$ResourceIdRetries\"\n"
            ")\n\n"
            "if (-not [string]::IsNullOrWhiteSpace($AppCollectionIDs)) {\n"
            "    $normalised = ($AppCollectionIDs -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ','\n"
            "    if ($normalised) {\n"
            "        $args += @(\"--app-collections\", $normalised)\n"
            "    }\n"
            "}\n\n"
            "$json = python /app/tasks/utils/sccm_admin.py @args\n"
            "$exit = $LASTEXITCODE\n\n"
            "Write-Output $json\n"
            "if ($exit -ne 0) { exit $exit }\n\n"
            "try {\n"
            "    $parsed = $json | ConvertFrom-Json\n"
            "    $global:SCCMResourceID     = $parsed.resource_id\n"
            "    $global:SCCMImportStatus   = $parsed.status\n"
            "    $global:SCCMAppCollections = $parsed.app_collections\n"
            "} catch { }\n"
        ),
        "p2": '[{"name":"VMName","type":"string","required":true},{"name":"OSCollectionID","type":"string","required":true},{"name":"MACAddress","type":"string","required":true},{"name":"SCCMGuiD","type":"string","required":true},{"name":"AppCollectionIDs","type":"string","required":false,"default":""},{"name":"ResourceIdRetries","type":"int","required":false,"default":"60"}]',
        "n3": "SCCM - Wait for Task Sequence",
        "d3": "Polls per-device deployment status until the task sequence completes, fails, or the timeout elapses.",
        "s3": (
            "param(\n"
            "    [Parameter(Mandatory=$true)][string]$VMName,\n"
            "    [Parameter(Mandatory=$true)][string]$OSCollectionID,\n"
            "    [int]$TimeoutMinutes = 360,\n"
            "    [int]$PollSeconds = 60\n"
            ")\n\n"
            "$json = python /app/tasks/utils/sccm_admin.py wait-task-sequence `\n"
            "    --name \"$VMName\" `\n"
            "    --os-collection \"$OSCollectionID\" `\n"
            "    --timeout-minutes \"$TimeoutMinutes\" `\n"
            "    --poll-seconds \"$PollSeconds\"\n"
            "$exit = $LASTEXITCODE\n\n"
            "Write-Output $json\n"
            "if ($exit -ne 0) { exit $exit }\n\n"
            "try {\n"
            "    $parsed = $json | ConvertFrom-Json\n"
            "    $global:SCCMLastStatus     = $parsed.status_description\n"
            "    $global:TaskSequenceResult = $parsed.result\n"
            "    $global:DeploymentID       = $parsed.deployment_id\n"
            "} catch { }\n"
        ),
        "p3": '[{"name":"VMName","type":"string","required":true},{"name":"OSCollectionID","type":"string","required":true},{"name":"TimeoutMinutes","type":"int","required":false,"default":"360"},{"name":"PollSeconds","type":"int","required":false,"default":"60"}]',
    })

    # ── Seed data: example script_modules (from 0096) ─────────────────────────
    _EXAMPLE_SCRIPTS = [
        {
            "name": "Example - Provision Asset",
            "description": "Example module: log provisioning context and signal success. Use as a starting point for real provision steps.",
            "script_type": "powershell",
            "script_content": (
                "param(\n"
                "    [Parameter(Mandatory=$true)]\n"
                "    [string]$asset_name,\n\n"
                "    [string]$asset_id,\n"
                "    [string]$order_id,\n"
                "    [string]$user_email,\n"
                "    [string]$user_name,\n"
                "    [string]$asset_type_name\n"
                ")\n\n"
                "$ErrorActionPreference = 'Stop'\n\n"
                "function Write-Log {\n"
                "    param([string]$Message, [string]$Level = 'INFO')\n"
                "    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'\n"
                "    Write-Host \"[$ts] [$Level] $Message\"\n"
                "}\n\n"
                "try {\n"
                "    Write-Log \"Starting provisioning for asset '$asset_name' (order $order_id)\"\n"
                "    Write-Log \"Asset type : $asset_type_name\"\n"
                "    Write-Log \"Assigned to: $user_name <$user_email>\"\n\n"
                "    # Add your provisioning logic here.\n"
                "    # Access global variables via $VARS, e.g.: $VARS.'my.server.host'\n\n"
                "    Write-Log \"Provision step completed successfully.\"\n"
                "    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)\n"
                "}\n"
                "catch {\n"
                "    Write-Log \"Provision step failed: $($_.Exception.Message)\" 'ERROR'\n"
                "    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)\n"
                "    exit 1\n"
                "}\n"
            ),
        },
        {
            "name": "Example - Change Asset",
            "description": "Example module: log change-request context and signal success. Use as a starting point for real change steps.",
            "script_type": "powershell",
            "script_content": (
                "param(\n"
                "    [Parameter(Mandatory=$true)]\n"
                "    [string]$asset_name,\n\n"
                "    [string]$asset_id,\n"
                "    [string]$order_id,\n"
                "    [string]$user_email,\n"
                "    [string]$user_name,\n"
                "    [string]$owner_email,\n"
                "    [string]$owner_name,\n"
                "    [string]$expires_at,\n"
                "    [string]$asset_type_name\n"
                ")\n\n"
                "$ErrorActionPreference = 'Stop'\n\n"
                "function Write-Log {\n"
                "    param([string]$Message, [string]$Level = 'INFO')\n"
                "    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'\n"
                "    Write-Host \"[$ts] [$Level] $Message\"\n"
                "}\n\n"
                "try {\n"
                "    Write-Log \"Starting change step for asset '$asset_name' (order $order_id)\"\n"
                "    Write-Log \"Current owner : $owner_name <$owner_email>\"\n"
                "    Write-Log \"New assignment: $user_name <$user_email>\"\n"
                "    Write-Log \"Expires       : $(if ($expires_at) { $expires_at } else { 'no expiry' })\"\n\n"
                "    # Add your change logic here.\n"
                "    # Access global variables via $VARS, e.g.: $VARS.'my.server.host'\n\n"
                "    Write-Log \"Change step completed successfully.\"\n"
                "    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)\n"
                "}\n"
                "catch {\n"
                "    Write-Log \"Change step failed: $($_.Exception.Message)\" 'ERROR'\n"
                "    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)\n"
                "    exit 1\n"
                "}\n"
            ),
        },
        {
            "name": "Example - Deprovision Asset",
            "description": "Example module: log deprovisioning context and signal success. Use as a starting point for real deprovision steps.",
            "script_type": "powershell",
            "script_content": (
                "param(\n"
                "    [Parameter(Mandatory=$true)]\n"
                "    [string]$asset_name,\n\n"
                "    [string]$asset_id,\n"
                "    [string]$order_id,\n"
                "    [string]$user_email,\n"
                "    [string]$user_name,\n"
                "    [string]$owner_email,\n"
                "    [string]$owner_name,\n"
                "    [string]$asset_type_name\n"
                ")\n\n"
                "$ErrorActionPreference = 'Stop'\n\n"
                "function Write-Log {\n"
                "    param([string]$Message, [string]$Level = 'INFO')\n"
                "    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'\n"
                "    Write-Host \"[$ts] [$Level] $Message\"\n"
                "}\n\n"
                "try {\n"
                "    Write-Log \"Starting deprovisioning for asset '$asset_name' (order $order_id)\"\n"
                "    Write-Log \"Returning from : $owner_name <$owner_email>\"\n"
                "    Write-Log \"Asset type     : $asset_type_name\"\n\n"
                "    # Add your deprovisioning logic here.\n"
                "    # Access global variables via $VARS, e.g.: $VARS.'my.server.host'\n\n"
                "    Write-Log \"Deprovision step completed successfully.\"\n"
                "    Write-Output (@{ success = $true; asset_name = $asset_name; order_id = $order_id } | ConvertTo-Json -Compress)\n"
                "}\n"
                "catch {\n"
                "    Write-Log \"Deprovision step failed: $($_.Exception.Message)\" 'ERROR'\n"
                "    Write-Output (@{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress)\n"
                "    exit 1\n"
                "}\n"
            ),
        },
    ]

    for script in _EXAMPLE_SCRIPTS:
        existing = conn.execute(sa_text("SELECT 1 FROM script_modules WHERE name = :n"), {"n": script["name"]}).scalar()
        if not existing:
            conn.execute(
                sa_text(
                    "INSERT INTO script_modules (name, description, script_content, script_type, is_active) "
                    "VALUES (:n, :d, :c, :t, true)"
                ),
                {"n": script["name"], "d": script["description"], "c": script["script_content"], "t": script["script_type"]},
            )

    # ── Seed scripts + runbooks from disk files (mirrors migration 0046) ───────
    import json as _json
    import logging as _logging
    from pathlib import Path as _Path

    _logger = _logging.getLogger("alembic.migration.0001.disk_seed")
    _EXT_TO_TYPE = {".ps1": "powershell", ".py": "python", ".sh": "bash"}
    _MODULES_DIR = _Path("/app/scripts/modules")
    _RUNBOOKS_DIR = _Path("/app/scripts/runbooks")

    def _parse_script_file(path):
        raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        name = ""
        desc = ""
        body_start = 0
        for i, line in enumerate(lines[:5]):
            stripped = line.strip()
            if stripped.startswith("# NAME:") and not name:
                name = stripped[len("# NAME:"):].strip()
                body_start = i + 1
            elif stripped.startswith("# DESC:"):
                desc = stripped[len("# DESC:"):].strip()
                body_start = i + 1
            elif stripped == "" and body_start == i:
                body_start = i + 1
        if not name:
            name = path.stem.replace("_", " ")
        script_type = _EXT_TO_TYPE.get(path.suffix.lower(), "powershell")
        body = "\n".join(lines[body_start:]).rstrip() + "\n"
        return name, desc, script_type, body

    if _MODULES_DIR.is_dir():
        files = sorted(_MODULES_DIR.rglob("*"))
        files = [f for f in files if f.is_file() and f.suffix.lower() in _EXT_TO_TYPE]
        for path in files:
            name, desc, script_type, content = _parse_script_file(path)
            existing = conn.execute(sa_text("SELECT 1 FROM script_modules WHERE name = :n"), {"n": name}).scalar()
            if existing:
                continue
            conn.execute(
                sa_text("INSERT INTO script_modules (name, description, script_content, script_type, is_active) VALUES (:n, :d, :c, :t, true)"),
                {"n": name, "d": desc or None, "c": content, "t": script_type},
            )
            _logger.info("seed: inserted script_modules row %r from %s", name, path.name)

    if _RUNBOOKS_DIR.is_dir():
        rows = conn.execute(sa_text("SELECT id, name FROM script_modules")).fetchall()
        name_to_id = {r[1]: r[0] for r in rows}
        for path in sorted(_RUNBOOKS_DIR.glob("*.json")):
            try:
                payload = _json.loads(path.read_text(encoding="utf-8"))
            except (OSError, _json.JSONDecodeError) as exc:
                _logger.warning("seed: could not read %s: %s", path, exc)
                continue
            if not isinstance(payload, dict) or not payload.get("name"):
                continue
            rb_name = str(payload["name"])
            existing = conn.execute(sa_text("SELECT 1 FROM standalone_runbooks WHERE name = :n"), {"n": rb_name}).scalar()
            if existing:
                continue
            rb_id = conn.execute(
                sa_text(
                    "INSERT INTO standalone_runbooks (name, description, is_active, cron_expression, cron_enabled, skip_if_running) "
                    "VALUES (:n, :d, :a, :ce, :cen, :sir) RETURNING id"
                ),
                {
                    "n": rb_name,
                    "d": payload.get("description") or None,
                    "a": bool(payload.get("is_active", True)),
                    "ce": payload.get("cron_expression") or None,
                    "cen": bool(payload.get("cron_enabled", False)),
                    "sir": bool(payload.get("skip_if_running", True)),
                },
            ).scalar_one()
            for step in payload.get("steps", []):
                script_name = step.get("script_module_name")
                script_id = name_to_id.get(script_name) if script_name else None
                conn.execute(
                    sa_text(
                        "INSERT INTO standalone_runbook_steps "
                        "(runbook_id, position, step_name, script_module_id, params_template, is_critical, retry_count, timeout_seconds, always_run) "
                        "VALUES (:rid, :pos, :sn, :smid, CAST(:pt AS json), :ic, :rc, :ts, :ar)"
                    ),
                    {
                        "rid": rb_id,
                        "pos": int(step.get("position", 0)),
                        "sn": step.get("step_name") or "",
                        "smid": script_id,
                        "pt": _json.dumps(step.get("params_template") or {}),
                        "ic": bool(step.get("is_critical", True)),
                        "rc": int(step.get("retry_count") or 3),
                        "ts": int(step.get("timeout_seconds") or 120),
                        "ar": bool(step.get("always_run", False)),
                    },
                )
            _logger.info("seed: inserted standalone_runbook %r from %s", rb_name, path.name)

    # Wire SCCM - Delete Device into Virtual Machine Recycler (from 0039)
    conn.execute(sa_text("""
        INSERT INTO standalone_runbook_steps
            (runbook_id, position, step_name, script_module_id, params_template,
             is_critical, retry_count, timeout_seconds)
        SELECT
            rb.id,
            COALESCE((SELECT MAX(position) FROM standalone_runbook_steps WHERE runbook_id = rb.id), 0) + 1,
            'SCCM - Delete Device',
            sm.id,
            CAST('{"VMName": "{{RecycleVmName}}"}' AS json),
            true, 3, 120
        FROM standalone_runbooks rb
        CROSS JOIN script_modules sm
        WHERE rb.name = 'Virtual Machine Recycler'
          AND sm.name = 'SCCM - Delete Device'
          AND NOT EXISTS (
              SELECT 1 FROM standalone_runbook_steps s
              WHERE s.runbook_id = rb.id AND s.script_module_id = sm.id
          )
    """))


def downgrade() -> None:
    pass
