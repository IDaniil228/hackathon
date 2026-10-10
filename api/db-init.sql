SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
ALTER ROLE :"role" WITH LOGIN PASSWORD :'password';
GRANT CONNECT ON DATABASE :"db" TO :"role";
GRANT USAGE ON SCHEMA core, ref, profile, assess, hire, match TO :"role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, ref, profile, assess, hire TO :"role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, ref, profile, assess, hire TO :"role";
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA match FROM :"role";
GRANT SELECT ON ALL TABLES IN SCHEMA match TO :"role";
GRANT SELECT ON public.alembic_version TO :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA core, ref, profile, assess, hire
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA core, ref, profile, assess, hire
    GRANT USAGE, SELECT ON SEQUENCES TO :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA match GRANT SELECT ON TABLES TO :"role";
