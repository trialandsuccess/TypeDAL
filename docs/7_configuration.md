# 7. Configuration

TypeDAL configuration is managed through `pyproject.toml`, `.env` file, environment variables, and `TypeDAL()` kwargs.
This page documents all available configuration options.

## Basic Setup

Configuration goes under `[tool.typedal]` in your `pyproject.toml`:

```toml
[tool.typedal]
database = "sqlite://path/to/database.sqlite"
folder = "databases"
caching = true
pool_size = 0
lazy_policy = "tolerate"
# keys may also be written as pool-size, lazy-policy
```

### Core Options

- **`database`**: Your database URI (required)
- **`folder`**: Directory for database files (default: `"databases"`). Primarily used by SQLite.
- **`caching`**: Enable query caching (default: `true`)
- **`pool_size`**: Connection pool size (default: `1` for sqlite, otherwise `3`). Set to `0` for no pooling.
- **`connection`**: Which connection config to use in multi-connection setups (default: `"default"`).
  See "Multiple Connections" below.
- **`lazy_policy`**: Default policy for implicit relationship loading.
  Values: `forbid`, `warn`, `ignore`, `tolerate`, `allow` (default: `"tolerate"`).
  Can be overridden per relationship. See [4. Relationships](./4_relationships.md) for details.

## Migrations

For the minimal configuration required to use migrations, see [6. Migrations](./6_migrations.md).
Options for generating and running migrations:

```toml
[tool.typedal]
input = "path/to/data_model.py"
output = "path/to/migrations.py"
dialect = "sqlite"
magic = true
function = "define_tables"
flag_location = "migrations/.flags"
migrate_table = "typedal_implemented_features"
create_flag_location = true
schema = "public"
migrate = false
fake_migrate = false
database_to_restore = "data/backup.sql"
tables = ["users", "posts"]
noop = false
```

### Generating Migrations (pydal2sql)

- **`input`**: Path to your TypeDAL table definitions file
- **`output`**: Path to the generated migration `.py` fil
- **`dialect`**: Database type: `sqlite`, `postgres`, `mysql`, etc. (if unclear from database uri)
- **`magic`**: Insert missing variables to prevent crashes (default: `true`).
  See [pydal2sql docs](https://github.com/robinvandernoord/pydal2sql#configuration).
- **`function`**: Function name containing your `db.define()` calls (default: `"define_tables"`)
- **`tables`**: Specific tables to generate migrations for (optional; usually set via CLI instead)
- **`noop`**: Don't write to output (usually set via CLI)

### Running Migrations (edwh-migrate)

- **`output`**: Path to the migration `.py` file containing your migrations
- **`flag_location`**: Directory where migration flags are stored
- **`create_flag_location`**: Auto-create flag location if missing (default: `true`)
- **`migrate_table`**: Table name tracking executed migrations (default: `"typedal_implemented_features"`)
- **`schema`**: Database schema to use (default: `"public"`, mainly for PostgreSQL)
- **`database_to_restore`**: Optional SQL file to restore before running migrations

For advanced options like `migrate_cat_command`, `schema_version`, and `redis_host`, see
the [edwh-migrate documentation](https://github.com/educationwarehouse/migrate#documentation).

### Migration Behavior

- **`migrate`**: Enable pydal's automatic migration behavior (default: `true`). Set to `false` in production to use
  manual migrations.
- **`fake_migrate`**: Mark migrations as executed without running them (default: `false`). Useful for initial setup or
  recovery.

For full details on pydal2sql options, see
the [pydal2sql configuration documentation](https://github.com/robinvandernoord/pydal2sql#configuration).

## Multiple Connections

Configure multiple database connections and switch between them:

```toml
[tool.typedal]
default = "development"

[tool.typedal.development]
database = "sqlite://"
dialect = "sqlite"
migrate = true

[tool.typedal.production]
dialect = "postgres"
migrate = false
```

```env
TYPEDAL_CONNECTION="production"
TYPEDAL_DATABASE="psql://user:password@host:5432/database"
```

- Set `default` to the connection used when `TYPEDAL_CONNECTION` is not set
- Each connection can have its own `[tool.typedal.connection_name]` section
- Environment variables override config values; use `TYPEDAL_CONNECTION` to switch active connections
- Secrets (like database URIs) should go in `.env` prefixed with `TYPEDAL_`

## Configuration Priority

TypeDAL loads configuration in this order (highest priority last):

1. **`pyproject.toml`** — Base configuration
2. **`.env` file** — Environment-specific overrides
3. **Environment variables** — System env vars with `TYPEDAL_` prefix (override `.env`)
4. **`TypeDAL()` kwargs** — Runtime arguments passed to the constructor

Example with all layers:

```toml
# pyproject.toml
[tool.typedal]
database = "sqlite://"
pool_size = 5
```

```env
# .env
TYPEDAL_DATABASE="psql://localhost/dev"
TYPEDAL_POOL_SIZE=10
```

```bash
# shell
export TYPEDAL_POOL_SIZE=20
```

```python
# code (highest priority)
db = TypeDAL(database="psql://prod/db")
```

The resulting config uses: `database="psql://prod/db"` and `pool_size=20` (from the environment variable).

## Environment Variables & Variable Interpolation

Override any config value using environment variables prefixed with `TYPEDAL_`:

```env
TYPEDAL_DATABASE="psql://..."
TYPEDAL_FOLDER="custom_folder"
TYPEDAL_POOL_SIZE=10
TYPEDAL_CACHING=false
TYPEDAL_LAZY_POLICY="forbid"
```

You can also use variable interpolation in your config to reference environment variables or `.env` values:

```toml
# pyproject.toml
[tool.typedal]
database = "psql://user:${DB_PASSWORD:defaultpass}@host:5432/database"
```

```env
# .env
DB_PASSWORD="secretpassword"
```

Use `${VAR:default}` syntax. If `VAR` is not set, `default` is used.

## TypeDAL() Constructor Options

Pass configuration directly when instantiating TypeDAL:

```python
from typedal import TypeDAL

db = TypeDAL(
    "sqlite://...",
    # *other pydal configuration
    # optional extra typedal settings:
    use_pyproject=True,
    use_env=True,
    connection="production",
    lazy_policy="forbid",
    enable_typedal_caching=True,
)
```

- **`use_pyproject`**: Load config from `pyproject.toml` (default: `True`). Set to a string path to use a custom file.
- **`use_env`**: Load config from `.env` and environment variables (default: `True`). Set to a string path to use a
  custom `.env` file, or `False` to disable.
- **`connection`**: Which connection to use in multi-connection setups
- **`lazy_policy`**: Override default lazy loading policy
- **`enable_typedal_caching`**: Override caching setting
- **`config`**: Pass a pre-built `TypeDALConfig` object directly

## Setup & Inspection

Generate a config interactively:

```bash
typedal setup        # full setup wizard
typedal setup --minimal  # skip non-essential prompts
```

View your current active configuration:

```bash
typedal --show-config
```

---

You've conquered the boring bits. 
Ready for something more interesting? 
Head to [8. Mixins](./8_mixins.md) to create powerful, reusable logic with mixins.
