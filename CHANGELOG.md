# Changelog

<!--next-version-placeholder-->

## v3.15.1 (2025-07-14)

### Fix

* **find_model:** Map both name and rname ([`e402d66`](https://github.com/trialandsuccess/TypeDAL/commit/e402d6659516d119fd3bd3fc28cea32b360aa068))

## v3.15.0 (2025-06-23)

### Feature

* **querybuilder:** Support dictionaries (AND) in where (table.where({"example": "selection, "with": "dictionary")) ([`67837f8`](https://github.com/trialandsuccess/TypeDAL/commit/67837f82b13246ca2a526141e8e3915167fe3369))

## v3.14.3 (2025-06-09)

### Fix

* Improved `point` parsing ([`9d8aa8a`](https://github.com/trialandsuccess/TypeDAL/commit/9d8aa8a9e23e09b5818153f0d9674176822d3893))

## v3.14.2 (2025-06-05)

### Fix

* **PointField:** Don't crash if invalid point is parsed ([`e14b4a3`](https://github.com/trialandsuccess/TypeDAL/commit/e14b4a349799799cc94e8fb62d9da9a0fafdcd36))

## v3.14.1 (2025-05-27)

### Fix

* Don't force slug if already manually set ([`a33b2b5`](https://github.com/trialandsuccess/TypeDAL/commit/a33b2b523a56ae9adc6fb4c49a5708efe7950977))

## v3.14.0 (2025-05-15)

### Feature

* `db.find_model` to get the registered TypedTable class for a specific 'table_name' ([`c645303`](https://github.com/trialandsuccess/TypeDAL/commit/c645303a85c96735f48eafaaeb20f867b977686f))

## v3.13.1 (2025-04-28)

### Fix

* Pass select kwargs via `.column()` - so you can do e.g. `distinct=True` ([`e5bc168`](https://github.com/trialandsuccess/TypeDAL/commit/e5bc168b90d6a5d214f049de0ef31e544214cc23))

## v3.13.0 (2025-04-28)

### Feature

* Adding `_once` hooks ([`a69fbb3`](https://github.com/trialandsuccess/TypeDAL/commit/a69fbb361cdfec9352fca503206299c5bbb940d2))

## v3.12.2 (2025-04-25)

### Fix

* Pinned slugify at wrong version ([`470c545`](https://github.com/trialandsuccess/TypeDAL/commit/470c545fd503afbd8d787b92fdf977f5e610333a))

## v3.12.1 (2025-04-25)

### Fix

* Support latest pydal version ([`5af1d2b`](https://github.com/trialandsuccess/TypeDAL/commit/5af1d2bbb8cd4188cfab840191816fc0de87cc4a))

## v3.12.0 (2025-04-25)

### Feature

* Support adding a `unique_alias` which improves working with multiple joins. ([`8b2112c`](https://github.com/trialandsuccess/TypeDAL/commit/8b2112cf9c97a00a9610e1af6e80e05fcc296aad))

## v3.11.1 (2025-04-25)

### Fix

* Improved typing (`mypy` is happy again, py4web DAL should be typed better now) ([`b3199cb`](https://github.com/trialandsuccess/TypeDAL/commit/b3199cb423f643b1826fedab45bcaa3063fdc02d))
* `repr(rows)` crashed when `rows` contained no data ([`cc3b2d0`](https://github.com/trialandsuccess/TypeDAL/commit/cc3b2d0bd332faee579e6fdfb7068bd511f22093))

## v3.11.0 (2025-04-25)

### Fix

* Add `._count()` to get sql for `.count()` ([`6a336e9`](https://github.com/trialandsuccess/TypeDAL/commit/6a336e903017a2fcfac9bea313f150e5af8d77de))
* Add `.exists()` method on querybuilder to check if there are any rows (using `.count()`) ([`7e54e64`](https://github.com/trialandsuccess/TypeDAL/commit/7e54e64461e04f6e65a9aa4d1736585f088bc9d1))

## v3.10.5 (2025-04-22)

### Fix

* `repr(rows)` crashed when `rows` contained no data ([`cc3b2d0`](https://github.com/trialandsuccess/TypeDAL/commit/cc3b2d0bd332faee579e6fdfb7068bd511f22093))

## v3.10.4 (2025-04-17)

### Fix

* Prevent duplicate callback hooks (before/after insert/update/delete) ([`e8e2271`](https://github.com/trialandsuccess/TypeDAL/commit/e8e2271da87d1993afc586060052dd5d057c28e1))

## v3.10.3 (2025-04-03)

### Fix

* **NativeUUIDField:** Don't try to parse uuid if it is falsey (null) ([`4dbca35`](https://github.com/trialandsuccess/TypeDAL/commit/4dbca3514efa13bdceb4367816f5deabf247de08))

## v3.10.2 (2025-03-25)

### Fix

* Alias TypeDAL.db to self (required for some Validators) ([`f230853`](https://github.com/trialandsuccess/TypeDAL/commit/f2308536d2206def844ac25277d01cdb503554ed))

## v3.10.1 (2025-02-17)

### Fix

* Specify pydal dependency before `20250215.1` because newer releases have breaking changes on table aliases ([`67b6466`](https://github.com/trialandsuccess/TypeDAL/commit/67b64669fce3ac9b43a0daa997f6e7c4c56d24f6))

## v3.10.0 (2025-02-17)

### Feature

* Add a pydal validator to tables using the slug mixin (without a random suffix) to catch duplicates before the actual database insert (which raises a unique violation exception) ([`6466345`](https://github.com/trialandsuccess/TypeDAL/commit/64663454eab7a4f281660cb2df6e50e2dadd7740))

## v3.9.4 (2024-11-10)

### Fix

* **p4w:** Calling `for_py4web.DAL()` without any arguments (to load from config) should work even with singleton ([`16f9e68`](https://github.com/trialandsuccess/TypeDAL/commit/16f9e681eaac2fccd06a110031c0d55261a0a7e9))

## v3.9.3 (2024-11-08)

### Fix

* **p4w:** Actually passing the 'uri' to connect to a database in singleton mode would be pretty useful no? ([`e29a0bb`](https://github.com/trialandsuccess/TypeDAL/commit/e29a0bbb1b3f954d7f8a3c13704952f8ec42a799))

## v3.9.2 (2024-11-08)

### Fix

* Py4web-specific DAL now provides a singleton per db_uid, so different apps using shared typedal classes share a db instance, leading to less weird behavior (e.g. on `db.commit()`) ([`a2af3d5`](https://github.com/trialandsuccess/TypeDAL/commit/a2af3d52a3f14ddddd40ec326df9c2705ef26eca))

## v3.9.1 (2024-10-25)

### Fix

* Bool(QueryBuilder) should NOT look at the data but if any filters were applied ([`962ddf3`](https://github.com/trialandsuccess/TypeDAL/commit/962ddf37f5d1ac014b73fd6349099100cc7efb5f))

## v3.9.0 (2024-10-25)

### Feature

* Add `condition_and` to .join so you can add additional requirements to inner joins ([`0ae688b`](https://github.com/trialandsuccess/TypeDAL/commit/0ae688ba01f421c70a660b5bb5d9672484494aa4))

## v3.8.5 (2024-10-24)

### Fix

* Use right 'timestamp' field ([`2aedb02`](https://github.com/trialandsuccess/TypeDAL/commit/2aedb027b4490f8284253e9c7f427b0660b6bc13))
* Allow specifying a field to Builder.count(...); support selecting extra fields (e.g. MyField.count()) ([`ce28a79`](https://github.com/trialandsuccess/TypeDAL/commit/ce28a7995a6d817424462f8b18383c85fa349ba4))

## v3.8.4 (2024-10-24)

### Fix

* Paginate with limit=0 will yield all rows instead of crashing pt2 ([`78f1ae7`](https://github.com/trialandsuccess/TypeDAL/commit/78f1ae7257af0ba90040a7356ad579d3c77aa231))

## v3.8.3 (2024-10-24)

### Fix

* Paginate with limit=0 will yield all rows instead of crashing ([`76813e6`](https://github.com/trialandsuccess/TypeDAL/commit/76813e63fc2a0915de2ae5aa3df5be0254678f8b))

## v3.8.2 (2024-10-23)

### Fix

* Improvements in relationship detection and multiple mixins
* make Mixin base class define __settings__ so other mixins can use them without checking for existance

## v3.8.1 (2024-10-22)

### Fix

* Make 'requires=' also accept list[Validator] or a single Validator/Callable ([`a4a7c00`](https://github.com/trialandsuccess/TypeDAL/commit/a4a7c002186f8824971987f96d573fe455dcd01d))

## v3.8.0 (2024-10-11)

### Feature

* Add `_sql()` function to TypedTable to generate SQL Schema code. (only if 'migration' extra/pydal2sql is installed) ([`31f86de`](https://github.com/trialandsuccess/TypeDAL/commit/31f86de30cc53cf320f6231c27dd545103b50d10))
* Add FieldSettings typed dict for better hinting for options when creating a TypedField() or any of the fields using it ([`97a7c7a`](https://github.com/trialandsuccess/TypeDAL/commit/97a7c7ad6112a6098088c44bbc6ae438bbfc0040))
* Add custom TypedFields for timestamp, point and uuid (valid types in postgres and sqlite is okay with anything) ([`a7bc9d1`](https://github.com/trialandsuccess/TypeDAL/commit/a7bc9d1b7ab0c88d4937956a68305b4d61a0851f))
* Started on custom types (timestamp) ([`981da83`](https://github.com/trialandsuccess/TypeDAL/commit/981da83cc8f4fec442b2cf74e0b555ce0633f96a))

## v3.7.1 (2024-10-09)

### Fix

* Prepare for python 3.13 (-> cgi dependency, changes in forward reference evaluation); except psycopg2 ([`bbcca8f`](https://github.com/trialandsuccess/TypeDAL/commit/bbcca8f7a5d2f8a6ddc8caf3a1b05fde3ed2fdd2))
* Require legacy-cgi for python 3.13+ ([`7ba9489`](https://github.com/trialandsuccess/TypeDAL/commit/7ba94898cde600008a350e718783a4d0dbc05e45))

### Documentation

* **readme:** Include `from typedal.helpers import get_db` in example ([`8853052`](https://github.com/trialandsuccess/TypeDAL/commit/8853052575b4576945901eb87da94bf709e99526))

## v3.7.0 (2024-08-17)

### Feature

* Add get_db, get_table, get_field helpers to get pydal objects back from typedal ([`2363e9d`](https://github.com/trialandsuccess/TypeDAL/commit/2363e9d3f15eb4500a0d5bc8354bf0326161bd1e))

## v3.6.0 (2024-08-05)

### Feature

* `typedal migrations.stub` (via pydal2sql) to generate edwh-migrate stub migration ([`67dbeb8`](https://github.com/trialandsuccess/TypeDAL/commit/67dbeb81ff4b28fa388688cdad31a42c4cd0cfc0))

## v3.5.0 (2024-07-03)

### Feature

* Improved callback hooks (before_/after_ insert/update/delete) ([`cabe328`](https://github.com/trialandsuccess/TypeDAL/commit/cabe328ddfc045e102b156de25c10cd9e91bd5ff))

## v3.4.0 (2024-06-04)

### Feature

* `.column()` function on querybuilder ([`173fb08`](https://github.com/trialandsuccess/TypeDAL/commit/173fb088458e887ea7df6b7bf720848a07ff949c))

## v3.3.1 (2024-06-04)

### Fix

* Typing improvements for relationships, with_alias ([`e76d3a3`](https://github.com/trialandsuccess/TypeDAL/commit/e76d3a37ad46c8a4a6bc18accabaab130bb0bd02))

### Documentation

* Manually append changelog ([`032b017`](https://github.com/trialandsuccess/TypeDAL/commit/032b017d4784e138e0065bac9fee22a13214c579))

## v3.3.0 (2024-05-22)

### Feature

* Improved type hinting ([`84e701d`](https://github.com/trialandsuccess/TypeDAL/commit/84e701d04938ae42955bbc898544e58bed302271))
* Improved limitby + orderby (or other select kwargs) combination, so sorting is done BEFORE limiting + tests to validate this behavior ([`84e701d`](https://github.com/trialandsuccess/TypeDAL/commit/84e701d04938ae42955bbc898544e58bed302271))

### Fix

* Validate_and_update does not raise an exception anymore, simplify function + make tests pass again ([`e7a33f6`](https://github.com/trialandsuccess/TypeDAL/commit/e7a33f684e08deacb51a50a088b1db42e9bf3a6b))

## v3.2.0 (2024-04-30)

### Feature

* Add .from_slug to SlugMixin ([`bacf416`](https://github.com/trialandsuccess/TypeDAL/commit/bacf4165b60e9a89dc0e5fc5d1801d499c1ffc6f))

## v3.1.4 (2024-04-22)

### Fix

* Re-used fields are now all bound separately ([`1b7f80b`](https://github.com/trialandsuccess/TypeDAL/commit/1b7f80b2013b6d55059df5a6700ec4327a96a5d6))

## v3.1.3 (2024-04-18)

### Fix

* **SlugMixin:** Field should not be manually writable ([`4254b77`](https://github.com/trialandsuccess/TypeDAL/commit/4254b77bb39a426a02d96e7a95c598662e6db744))

## v3.1.2 (2024-04-17)

### Fix

* Bump `pydal2sql` for better CREATE statements (when a callable 'default' value is used) ([`28370f3`](https://github.com/trialandsuccess/TypeDAL/commit/28370f32828b1e7c6def039f109e2e8770a1a074))

## v3.1.1 (2024-04-16)

### Documentation

* More info about configuration and the cli ([`950295d`](https://github.com/trialandsuccess/TypeDAL/commit/950295d1003a053ffa9590292a8678435a379490))

## v3.1.0 (2024-04-16)

### Feature

* Added `Mixins` ([`75d69da`](https://github.com/trialandsuccess/TypeDAL/commit/75d69da1bb0ea73fa208dfda2328ca09719c4cc9))

### Fix

* **deps:** Edwh-migrate 0.8 is release so don't depend on alpha version anymore ([`4b324ec`](https://github.com/trialandsuccess/TypeDAL/commit/4b324ec399786675636872850a616553911468c5))

## v3.0.1 (2024-04-16)

### Fix

* Don't add before_update/before_delete hooks if caching is disabled ([`2c15bf2`](https://github.com/trialandsuccess/TypeDAL/commit/2c15bf226281b39e8c0533c441eaab3d7142f6e6))

## v3.0.0 (2024-04-02)



## v3.0.0-beta.4 (2024-03-20)

### Fix

* Bump pydal2sql dependency ([`33ba73e`](https://github.com/trialandsuccess/TypeDAL/commit/33ba73edbba4d0cf807a9a99846fb0b79d608262))

## v3.0.0-beta.3 (2024-03-20)

### Fix

* Swap out black's implementation of find_pyproject_toml to configuraptor's ([`82676cc`](https://github.com/trialandsuccess/TypeDAL/commit/82676cc41f8734bc4e317b8f2b17f749024884ac))

## v3.0.0-beta.2 (2024-03-20)

### Fix

* Use skip_none for configuraptor.update to prevent setting optional fields to None ([`b05caf8`](https://github.com/trialandsuccess/TypeDAL/commit/b05caf8bf5492a58fb4173b3a265f573b3dd0f94))

## v3.0.0-beta.1 (2024-03-20)

### Feature

* Added `migrations.fake` subcommand to mark edwh-migrate migrations as done in the db ([`1a39409`](https://github.com/trialandsuccess/TypeDAL/commit/1a39409efec775f15405ab9789a50c91a8801338))
* Allow --format/--fmt/-f for cache stats to output as machine-readable + more docstrings ([`8cd2771`](https://github.com/trialandsuccess/TypeDAL/commit/8cd27713e351ac846ca11944dfba36783724c2c6))
* Initial basic statistics via cli (typedal cache.stats) ([`5822763`](https://github.com/trialandsuccess/TypeDAL/commit/58227631ff1c2290aab90d40418c80bc4e4e278d))

### Fix

* Make pytest work again and other su6 tests happier ([`ab4061e`](https://github.com/trialandsuccess/TypeDAL/commit/ab4061eb796d21d90502fe8e9b88ca36e1c7613c))

## v2.4.0 (2024-02-26)

### Feature

* **querybuilder:** New .execute() to execute a query from the builder raw, without any postprocessing ([`68d631c`](https://github.com/trialandsuccess/TypeDAL/commit/68d631c0d6afb4fa6895b282adc8a863b901c3fd))

## v2.3.6 (2024-01-01)

### Fix

* Passing folder=Path() to TypeDAL() shouldn't crash config ([`7bcf96d`](https://github.com/trialandsuccess/TypeDAL/commit/7bcf96d48ca34faaa4ad3355ce699be62aaf7e94))

## v2.3.5 (2023-12-19)

### Fix

* Some fields should not be nullable ([`4a3ebba`](https://github.com/trialandsuccess/TypeDAL/commit/4a3ebba31eea0da18e4f789644af44a099b1711d))

## v2.3.4 (2023-12-19)

### Fix

* Included web2py auth tables in for_web2py ([`b7c7213`](https://github.com/trialandsuccess/TypeDAL/commit/b7c7213c59a253bc2211ab3771be2e3fa7d9f7a3))

## v2.3.3 (2023-12-19)



## v2.3.3-beta.1 (2023-12-19)

### Fix

* Remove json-fix magic, just use the custom json serializer instead! ([`6f5b687`](https://github.com/trialandsuccess/TypeDAL/commit/6f5b68751b97d7e69f914563c8ccbf282cab9527))

## v2.3.2 (2023-12-18)

### Fix

* Improved JSON dumping with custom logic instead of pydals logic ([`ab9c3f0`](https://github.com/trialandsuccess/TypeDAL/commit/ab9c3f0be637942a472f4ffdba3effafc62460f5))

## v2.3.1 (2023-12-18)

### Fix

* Model classes can now have non-db properties and methods and pydal will not complain anymore. Config should all go via @define, not as class properties anymore ([`49db4df`](https://github.com/trialandsuccess/TypeDAL/commit/49db4df2ff4c7ad2e9dad6cb78fab7ca7034a7d0))

## v2.3.0 (2023-12-18)

### Feature

* Db._config exposes the config used by TypeDAL ([`276fd8e`](https://github.com/trialandsuccess/TypeDAL/commit/276fd8e426bb1923146d681cf71e4bd0b9a4c577))
* .update and .delete on a TypedRows result (after .collect()) ([`813f008`](https://github.com/trialandsuccess/TypeDAL/commit/813f008fbb0cb5b82c1c321d058bace25b83a23c))

### Fix

* Cached items can now be dumped .to_json() ([`0eaacc5`](https://github.com/trialandsuccess/TypeDAL/commit/0eaacc5ebdcc3648231ff9fc5a10436e6ec9d853))

## v2.2.4 (2023-12-15)

### Fix

* If no .env, still load os environ pt2 ([`3a04890`](https://github.com/trialandsuccess/TypeDAL/commit/3a048903ed9e0f3bbe54b8e01ab68193c4d17a71))

## v2.2.3 (2023-12-15)

### Fix

* If no .env, still load os environ ([`983c5b4`](https://github.com/trialandsuccess/TypeDAL/commit/983c5b42fd2d654236e77578f21bd94d920992a0))

## v2.2.2 (2023-12-14)

### Feature

* Env vars are now filled into config toml settings ([`dfbca8f`](https://github.com/trialandsuccess/TypeDAL/commit/dfbca8fa316b44758ae13c51b54386649ff72979))

## v2.2.1 (2023-12-05)

### Fix

* **py4web:** Add proper requires= to pydal's auth_user model ([`00d6436`](https://github.com/trialandsuccess/TypeDAL/commit/00d643601c2ee90cad19951a7877163ef97b2b4e))

### Documentation

* **readme:** Removed roadmap and set new docs url ([`6045065`](https://github.com/trialandsuccess/TypeDAL/commit/6045065b76a2b7de47afdd6386fdeca204e58af2))

## v2.2.0 (2023-12-05)
### Feature
* **cli:** Started with `setup` cli command to initialize typedal config ([`a46a859`](https://github.com/trialandsuccess/TypeDAL/commit/a46a859216b02dc2841d75c9391d5b3f52036daf))
* Start working on cli that unifies pydal2sql + edwh-migrate ([`180dbb1`](https://github.com/trialandsuccess/TypeDAL/commit/180dbb1dd5738e312fbadb8a6ebea445115d96f5))
* **config:** TypeDAL will now by default look for config in pyproject.toml and env (.env and os environ) ([`6af3cae`](https://github.com/trialandsuccess/TypeDAL/commit/6af3cae3bc1910ea6b3e145b107765fe31a60897))

### Fix
* **install:** Typedal[all] should now include all dependencies ([`e46b3ce`](https://github.com/trialandsuccess/TypeDAL/commit/e46b3ceadba8d0196254c38b0a913e3fee09046a))
* **setup:** Tomlkit uses custom types which broke setup ([`7a6eca1`](https://github.com/trialandsuccess/TypeDAL/commit/7a6eca1b7ead7ee1396e85a2df019788900941a0))
* Improve cli + tests ([`978fea1`](https://github.com/trialandsuccess/TypeDAL/commit/978fea1c158b6b1df3f0e79e9ffcd872defac0d4))

### Documentation
* **migrations:** Included subsection about multiple connections ([`acc8922`](https://github.com/trialandsuccess/TypeDAL/commit/acc892246f6f4297c53f878dca1212e4c9530c2c))
* Added section 6 on Migrations ([`2423ed8`](https://github.com/trialandsuccess/TypeDAL/commit/2423ed856d0fade11c4e42e8846a93aaa1f4d59f))

## v2.1.5 (2023-11-21)

### Feature

* Automatically create the typedal 'folder' if it doesn't exist yet ([`163b205`](https://github.com/trialandsuccess/TypeDAL/commit/163b20548f73b764d688137efae87f060a29ec4d))

### Fix

* **cache:** If cache table exists but flags are missing, properly re-define with fake migrate ([`212eb8a`](https://github.com/trialandsuccess/TypeDAL/commit/212eb8a8d75afc995cf2fa0346b19a2a91d0fd56))

## v2.1.4 (2023-11-07)

### Fix

* Readthedocs was missing dependencies ([`02e8109`](https://github.com/trialandsuccess/TypeDAL/commit/02e81094dea0026226683148e951203f91da1d70))

### Documentation

* Set new docs url on pypi ([`3b6ea5b`](https://github.com/trialandsuccess/TypeDAL/commit/3b6ea5bceecb434eee0d32378321abb8d8efbc69))
* Renamed readme to index so RTD understands it better ([`9d544a3`](https://github.com/trialandsuccess/TypeDAL/commit/9d544a321ad7ebc4cf6d083598173de06e7d2462))
* Add ReadTheDocs with mkdocs ([`e2d2e48`](https://github.com/trialandsuccess/TypeDAL/commit/e2d2e48ca8fa0fd8116396e6fe07a53281622bb6))

## v2.1.3 (2023-11-02)

### Fix

* **mypy:** TypeDAL(folder=...) folder can also be a Path ([`f7494ed`](https://github.com/trialandsuccess/TypeDAL/commit/f7494ed3a3cae561ee8113ea3af1b57d4973b8b5))

## v2.1.2 (2023-11-02)

### Fix

* Auto-fake internal tables if migrating fails ([`4a87190`](https://github.com/trialandsuccess/TypeDAL/commit/4a871904fb75fbcd077f2b6e261b5c767b996200))

## v2.1.1 (2023-11-02)

### Documentation

* Update changelog for 2.1 release ([`6affca5`](https://github.com/trialandsuccess/TypeDAL/commit/6affca5bec1831dabe55e9025eb2b50b3ce46e2c))

## v2.1.0 (2023-11-02)

### Feature

* **p4w:** Add AuthUser class ([`d355314`](https://github.com/trialandsuccess/TypeDAL/commit/d355314686aebf57b0fd2babeae6c4cd07207264))
* Started implementing .cache() on query builder ([`a2687dc`](https://github.com/trialandsuccess/TypeDAL/commit/a2687dc8835547ff98e6ed668c428315a7830661))
* **caching:** You can now mark a table as "not a caching dependency" with db.define(cache_dependency=False) ([`e1ad350`](https://github.com/trialandsuccess/TypeDAL/commit/e1ad350fc688c1cadd3c5f49a79316eb1fd9bc6f))
* **caching:** Allow setting ttl/expire datetime for cache ([`c3b4671`](https://github.com/trialandsuccess/TypeDAL/commit/c3b4671a6abcf26dfb1b177d91c0f5b30bd5fd97))
* Caching requires dill ([`a7e5ca8`](https://github.com/trialandsuccess/TypeDAL/commit/a7e5ca81bf3076ef53cc1258751bbab39e75ad75))

### Documentation

* Explained you can pass kwargs to db.define ([`7d6591c`](https://github.com/trialandsuccess/TypeDAL/commit/7d6591c364dfb751fccd20acef00a036f770a754))
* Added .cache() to the query builder docs ([`60f8f93`](https://github.com/trialandsuccess/TypeDAL/commit/60f8f93ae91583515316532c7c1cfcd0accfe82e))
* You need `redefine=True` when extending AuthUser ([`29c63a3`](https://github.com/trialandsuccess/TypeDAL/commit/29c63a32e4c68026b750b625265a33d0664bb873))
* Remove 2.1 from roadmap in preparation for 2.1 release ([`202a028`](https://github.com/trialandsuccess/TypeDAL/commit/202a0285a51eb157445ef851c84ed55efcd1fb30))
* Add a small chapter about py4web integration ([`cb838d8`](https://github.com/trialandsuccess/TypeDAL/commit/cb838d8af47f10abaea212db674bdb0520ef7e14))
* Cleaned up changelog ([`8dad225`](https://github.com/trialandsuccess/TypeDAL/commit/8dad2251cc40ee655d984549fb887e2f09233954))

## v2.0.2 (2023-11-01)

### Fix

* Proper capitalization on pypi ([`8a0c320`](https://github.com/trialandsuccess/TypeDAL/commit/8a0c32043b2d0e05915f6cf3ff8f33da1b227b6a))

### Documentation

* Updated readme ([`7706e75`](https://github.com/trialandsuccess/TypeDAL/commit/7706e75053c1333f850ccd59c089690f7f3b1c4e))
* Added one-to-one relationship example (and included it in tests) ([`dac1721`](https://github.com/trialandsuccess/TypeDAL/commit/dac172146f7e97caa212eb277a9971bae565faf7))
* Fix typo ([`77981c9`](https://github.com/trialandsuccess/TypeDAL/commit/77981c9cabe354e78a10fe460bdd3ec2d6354316))

## v2.0.1 (2023-11-01)

### Fix

* Or_fail functions can now be passed a specific error ([`75b49e9`](https://github.com/trialandsuccess/TypeDAL/commit/75b49e973ad884ab282571ef4122d1041e81cb06))

### Documentation

* Set urls from relative to absolute so they work on pypi ([`d71e7c4`](https://github.com/trialandsuccess/TypeDAL/commit/d71e7c43a19001774a0296d4fbc0515c812d465b))
* Merged prerelease changelog entries into one 2.0.0 entry ([`f2d73e1`](https://github.com/trialandsuccess/TypeDAL/commit/f2d73e1f256ed21d1a8f2f6499a155d4a8c4c05d))

## v2.0.0 (2023-11-01)

### Features

* **p4w:** Add py4web specific DAL Fixture ([`2d87327`](https://github.com/trialandsuccess/TypeDAL/commit/2d8732746042b14d02c96c5c3c8e22393bb7697c))
* Added more ORM features (dynamic querying. some better typing etc) ([`64c9b67`](https://github.com/trialandsuccess/TypeDAL/commit/64c9b67085dc821bfc7c90a5e75d5c9489edc4d9))
* More progress on Typed ORM logic. BREAKS A LOT OF PYTESTS!!! ([`582cb56`](https://github.com/trialandsuccess/TypeDAL/commit/582cb56fa122c0cb8ba5df79402af1fab281b535))
* **orm:** Allows creating relationships between tables ([`5902ac6`](https://github.com/trialandsuccess/TypeDAL/commit/5902ac685daeb73c7fd8dd653407763dec18407d))
* Extended Rows (result) functionality ([`8a20c0c`](https://github.com/trialandsuccess/TypeDAL/commit/8a20c0cb4fe8ebc871ff45106c2089b932ad6a03))
* **rows:** Added typedrow functionality + tests ([`22ee99b`](https://github.com/trialandsuccess/TypeDAL/commit/22ee99b493d378075a4a253bad31a92ae8751c99))
* Improvements on row instances ([`218a45e`](https://github.com/trialandsuccess/TypeDAL/commit/218a45eeff5a90dbaeda0a9d82d0873463685516))
* **typedrows:** Added metadata for debugging etc ([`658ecfe`](https://github.com/trialandsuccess/TypeDAL/commit/658ecfe4cb1272605a5e2b395e2cd354d802b965))
* **query:** Improved INNER JOIN handling and allow _method to generate SQL ([`ab5794b`](https://github.com/trialandsuccess/TypeDAL/commit/ab5794b4af863f79dcaa2662c060e8b36c50219f))
* **json:** Compatibility with json-fix ([`d9ca1af`](https://github.com/trialandsuccess/TypeDAL/commit/d9ca1afb09ce58c11b79f71a4c4049049d8f81c4))
* **querybuilder:** Add pagination ([`6adf622`](https://github.com/trialandsuccess/TypeDAL/commit/6adf622bfe7af2bcf197c2a8ea4f962276cc96a1))
* **paginate:** As_dict for PaginatedRows now also returns pagination info in addition to data ([`3b17937`](https://github.com/trialandsuccess/TypeDAL/commit/3b17937b51e68c8bce9c031969702581fd4bcc3b))
* **paginate:** Paginate doesn't return a querybuilder anymore but can replace collect, returns a PaginatedRows ([`ccda5d4`](https://github.com/trialandsuccess/TypeDAL/commit/ccda5d4d9d6e94272472ede0e749fc7b84f46e01))
* **table:** Shadowed and/or modified pydal.Table methods in TypedTable (Meta) ([`397bf67`](https://github.com/trialandsuccess/TypeDAL/commit/397bf675710fc938402151599b0d7887f1ba9d62))
* Add .chunk to query builder + chore more tests ([`51f5979`](https://github.com/trialandsuccess/TypeDAL/commit/51f5979e003733f0f7074e5629d63ac5e3eb71f7))

### Fix

* **json:** Auto add json-fix if including for py4web ([`4d51511`](https://github.com/trialandsuccess/TypeDAL/commit/4d515111dc66071f69848207880cd4c609947747))
* **core:** Table(0) can also work in some instances, so don't check for falsey but for None ([`4430453`](https://github.com/trialandsuccess/TypeDAL/commit/4430453495e4d9e594cd48547707e4d4bb0e2ff2))
* **select:** Auto add missing id in select ([`eacce47`](https://github.com/trialandsuccess/TypeDAL/commit/eacce474f1950e776700899274ffad34e251d6bf))
* **relations:** One-to-one improvements ([`65984ce`](https://github.com/trialandsuccess/TypeDAL/commit/65984cec839351f9800a7a58c06cf95f65b3cf5a))
* **relationships:** Better collection ([`3db63ab`](https://github.com/trialandsuccess/TypeDAL/commit/3db63ab7c6eaa5da5b3639313b5afaa0f62d8ca0))
* **mypy:** Fix return types in fields.py ([`51322f7`](https://github.com/trialandsuccess/TypeDAL/commit/51322f7405cba73fd721c590914a3acde4c6716e))

### Documentation

* Started with more documentation about the ORM features ([`ca86f7c`](https://github.com/trialandsuccess/TypeDAL/commit/ca86f7c271c4e5ee0091ff2818643586f3fe7ce3))
* Minor improvements in examples and text ([`76d0bae`](https://github.com/trialandsuccess/TypeDAL/commit/76d0baee6b6fda551b04032e0f49134bd3be8ef4))
* Updated examples and roadmap ([`55901fd`](https://github.com/trialandsuccess/TypeDAL/commit/55901fd0ee6af1bb9b0e28088c8eb15647f6b8e1))
* **relationships:** Added examples of one-to-many, many-to-one, many-to-many ([`2ff8555`](https://github.com/trialandsuccess/TypeDAL/commit/2ff8555a605b4978c5bfa146bf04d7c3a305b0c0))
* **queries:** Explain select, where, join + new paginated rows ([`6d2b9b8`](https://github.com/trialandsuccess/TypeDAL/commit/6d2b9b8bde26bbbd0e72fc250c0f515b93b2fa89))


## v1.2.2 (2023-07-06)
### Fix

* Allow using `TypedFieldType`s in .select() ([`2945d15`](https://github.com/trialandsuccess/TypeDAL/commit/2945d15b3856f4881d27bd00504b3a2b7363b7ed))

## v1.2.1 (2023-06-27)
### Fix

* Typehint query options for db() and update_or_insert() ([`2b65f58`](https://github.com/trialandsuccess/TypeDAL/commit/2b65f58ccf93f12713877fd420ec43454911efe9))

## v1.2.0 (2023-06-27)
### Feature

* **set:** Type hints for .count() and .select() ([`1f23581`](https://github.com/trialandsuccess/TypeDAL/commit/1f2358169c6690e36153e99983c53a6c370f5e40))

## v1.1.2 (2023-06-27)
### Fix

* **query:** A class with postponed db.define could not be used to make queries ([`2e55363`](https://github.com/trialandsuccess/TypeDAL/commit/2e553632312fd759702e220ae71de9e0abb3d5a9))

## v1.1.1 (2023-06-22)
### Fix

* **mypy:** Made the package properly typed ([`a242895`](https://github.com/trialandsuccess/TypeDAL/commit/a242895d9d8a2d04add9c863105c2b1c756a3b6a))

## v1.1.0 (2023-06-22)
### Feature

* **types:** You should use `param = TypedField()` instead of `param: TypedField()` from now on for better mypy support ([`ed50273`](https://github.com/trialandsuccess/TypeDAL/commit/ed50273ded4a178a30ec8c9d6f492b871853a568))

### Fix

* **version:** We were already on 1.0.0 ([`d6a8d0d`](https://github.com/trialandsuccess/TypeDAL/commit/d6a8d0d17274a5a89e7c0260ba00bb916533866c))

### Documentation

* More info about new release ([`ddfffe2`](https://github.com/trialandsuccess/TypeDAL/commit/ddfffe2c5e6a8f7799449d059e5a2b8dd9c95dab))

## v1.0.0 (2023-06-05)
Refactored pre-1.0 code and added [su6](https://github.com/robinvandernoord/su6-checker) checker tool.  
Moved to `pyproject.toml` based build system.

### Feature
* **su6:** Add github workflow with checkers ([`d462387`](https://github.com/trialandsuccess/TypeDAL/commit/d46238705b27137ed60806eca5c53d8c2940052f))
* **tests:** Moved tests.py to pytest ([`de33d20`](https://github.com/trialandsuccess/TypeDAL/commit/de33d2053e6499c4a8628cfc4b8813b5a649c584))
* **define:** Pass extra kwargs (e.g. format) to define_table ([`bb692dc`](https://github.com/trialandsuccess/TypeDAL/commit/bb692dc1b2a9c18e45bcb70771b7435df8a39c8e))
* **TypedRows:** Subscriptable return type for .select() ([`7b674b0`](https://github.com/trialandsuccess/TypeDAL/commit/7b674b07d4e61f4ead5d3f006e018b1aa66c10e7))
* Add specific Fields, experimented with __future__.annotations (does not work right now) and general improvements ([`9af27c2`](https://github.com/trialandsuccess/TypeDAL/commit/9af27c254bf2ad7bd3bfa80e66b6e08e82f8f13a))

### Fix
* **gh:** Also install current project with dependencies on checking ([`60a1010`](https://github.com/trialandsuccess/TypeDAL/commit/60a101064b32cead856dd1a0d1a78a2e74c94abf))
* **ruff:** Make linter happy ([`b961054`](https://github.com/trialandsuccess/TypeDAL/commit/b9610542d199a42b527a58aaf08ddf70025abe6e))
* **..Field:** Use typing.Type[] instead of type() for better hinting ([`308ea14`](https://github.com/trialandsuccess/TypeDAL/commit/308ea14f38d4611b626c0edaf737ee53467b25b2))
* **core:** Table(id) works now just like Table(id=id) ([`c19e170`](https://github.com/trialandsuccess/TypeDAL/commit/c19e17074edfba06287402d0c52d52127fe15499))
* **intfield:** Return int type, not int instance ([`334c3ac`](https://github.com/trialandsuccess/TypeDAL/commit/334c3acd9f0e38badb742a69181e19ebf2088cb1))
* **core:** Notnull > required, fix ReferenceField with cls instead of string ([`1cc1482`](https://github.com/trialandsuccess/TypeDAL/commit/1cc148209572ce1d457bfde51f86c9167b2da904))
* Actually use custom type in TypedField ([`98c8fc3`](https://github.com/trialandsuccess/TypeDAL/commit/98c8fc30c311136b4804ed3211884b70dd7a98af))
