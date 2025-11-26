# 6. Migrations

By default, pydal manages migrations in your database schema
automatically ([See also: the web2py docs](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Migrations)).
This can be problematic in a production environment where you want to disable automatic table changes and manage these
by hand.

TypeDAL integrates with [`edwh-migrate`](https://pypi.org/project/edwh-migrate/) to make this easier. With this tool,
you write migrations (`CREATE`, `ALTER`, `DROP` statements) in SQL and it keeps track of which actions have already been
executed on your database.

To make this process easier, TypeDAL also integrates with [`pydal2sql`](https://pypi.org/project/pydal2sql/), which can
convert your pydal/TypeDAL table definitions into `CREATE` statements for new tables, or `ALTER` statements for existing
tables.

## Installation

To enable the migrations functionality within TypeDAL, install it with the migrations extra:

```bash
pip install typedal[migrations] # also included in typedal[all]
```

## Minimal Configuration

To use migrations, you need to configure TypeDAL in your `pyproject.toml`. At minimum, you must set:

- `database`: Your database URI
- `dialect`: The database type (e.g., `sqlite`, `postgres`)
- `migrate`: Set to `false` to disable pydal's automatic migrations
- `flag_location`: Where edwh-migrate stores its migration tracking
- `input`: Path to your table definitions (e.g., `data_model.py`)
- `output`: Where generated migrations are written (e.g., `migrations/`)

Optionally:

- `database_to_restore`: Path to a SQL file to restore before running migrations on a fresh database.

Here's a minimal example:

```toml
[tool.typedal]
database = "sqlite://"
dialect = "sqlite"
migrate = false
flag_location = "migrations/.flags"
input = "path/to/data_model.py"
output = "path/to/migrations.py"
```

For dynamic properties or secrets (like a database with credentials), 
add them to your `.env` file or set them as environment variables (optionally prefixed with `TYPEDAL_`):

```env
TYPEDAL_DATABASE = "psql://user:password@host:5432/database"
```

> **Full configuration reference**: For all available options, multiple connections, environment overrides, and other
> settings, see [7. Configuration](./7_configuration.md).

You can generate a config interactively with `typedal setup`, or view your current config with `typedal --show-config`.

## Generate Migrations (pydal2sql)

With your config in place, generate migrations from your table definitions:

```bash
typedal migrations.generate
```

You can override config values with CLI flags. See all options:

```bash
typedal migrations.generate --help
```

## Run Migrations (edwh-migrate)

Apply your migrations to the database:

```bash
typedal migrations.run
```

With a correctly configured setup, this should function without extra arguments.
You can however overwrite the behavior as defined in the config. See all options:

```bash
typedal migrations.run --help
```
