# Current database bootstrap

Empty databases are initialized only by:

```text
python -m knowledge_mining.mining.maintenance.database_upgrade apply
```

The command applies the cleaned, explicit Mining/KB schema path list, the LLM
PostgreSQL schema, and the Java-owned bootstrap SQL before recording the
versioned migrations. Business-service startup is read-only and must never run
these files.

This directory is the policy marker for the current baseline. Historical SQL
outside the explicit bootstrap lists is not discovered by globbing.
