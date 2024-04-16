# 6. Migrations

By default, pydal manages migrations in your database schema
automatically ([See also: the web2py docs](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Migrations)).
This can however be problemantic in a more production environment.
In some cases, you want to disable automatic table changes and manage these by hand.

TypeDAL integrates with [`edwh-migrate`](https://pypi.org/project/edwh-migrate/) to make this easier.
With this tool, you write migrations (`CREATE`, `ALTER`, `DROP` statements) in SQL and it keeps track of which actions
have already been executed on your database.

In order to make this process easier, TypeDAL also integrates
with [`pydal2sql`](https://pypi.org/project/pydal2sql/), which can convert your pydal/TypeDAL table definitions
into `CREATE` statements if it's a new table, or `ALTER` statements if it's an existing table.

## Installation

To enable the migrations functionality within TypeDAL, you'll need to install it with the specific migrations extra
dependencies. Run the following command:

```bash
pip install typedal[migrations] # also included in typedal[all]
```

This extra option is necessary as it adds a few dependencies that aren't essential for the core functionality of
TypeDAL. Enabling the migrations explicitly ensures that you have the additional tools and features available for
managing migrations effectively.

## Config

TypeDAL's migration behavior and some other features can be customized using a section in your `pyproject.toml`.
An example config can look like this:

```toml
[tool.typedal]
database = "storage.sqlite"
dialect = "sqlite"
folder = "databases"
caching = true
pool_size = 1
database_to_restore = "data/backup.sql"
migrate_table = "typedal_implemented_features"
flag_location = "databases/flags"
create_flag_location = true
schema = "public"
migrate = false  # disable pydal's automatic migration behavior
fake_migrate = false
```

To generate such a configuration interactively, use `typedal setup`. If you already have `[tool.pydal2sql]`
and/or `[tool.migrate]` sections, setup will incorporate their settings as defaults. For only essential prompts, add
`--minimal`; sensible defaults will fill in the rest.

For dynamic properties or secrets (like a database in postgres with credentials), exclude them from the toml and add
them to your .env file (optionally prefixed with `TYPEDAL`_):

```env
TYPEDAL_DATABASE = "psql://user:password@host:5432/database"
```

Settings passed directly to `TypeDAL()` will overwrite config values.

### Multiple Connections

Thie configuration allows you to define multiple database connections and specify which one `TypeDAL()` will use through environment
variables.

```toml
[tool.typedal]
default = "development"

[tool.typedal.development]
database = "sqlite://"
dialect = "sqlite"
migrate = true

[tool.typedal.production]
# database from .env
dialect = "postgres"
migrate = false
```

```env
TYPEDAL_CONNECTION="production"
TYPEDAL_DATABASE="psql://..."
```

In the pyproject.toml file, under` [tool.typedal]`, you can set a default connection key, which here is set to "
development".
This key corresponds to a section named `[tool.typedal.development]` where you define configuration details for the
development environment, such as the database URL (database) and dialect.

Similarly, another section `[tool.typedal.production]` holds configuration details for the production environment. In
this
example, the database parameter is fetched from the `.env` file using the environment variable `TYPEDAL_DATABASE`. The
`dialect` specifies the type of database being used, and `migrate` is set to `false` here, disabling automatic
migrations in the production environment.

The .env file contains environment variables like `TYPEDAL_CONNECTION`, which dictates the current active connection 
("production" in this case), and `TYPEDAL_DATABASE`, holding the database URI for the production environment.

This setup allows you to easily switch between different database configurations by changing the `TYPEDAL_CONNECTION`
variable in the `.env` file, enabling you to seamlessly manage different database settings for distinct environments like
development, testing, and production while keeping every (non-secret) config setting documented.

To see the currently active configuration settings, you can run `typedal --show-config`.

## Generate Migrations (pydal2sql)

Assuming your configuration is properly set up, `typedal migrations.generate` should execute without additional
arguments.
You can however overwrite the behavior as defined in the config. See the following command for all options:

```bash
typedal migrations.generate --help
```

## Run Migrations (edwh-migrate)

With a correctly configured setup, running `typedal migrations.run` should function without extra arguments.
You can however overwrite the behavior as defined in the config. See the following command for all options:

```bash
typedal migrations.run --help
```
