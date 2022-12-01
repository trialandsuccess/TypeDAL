# TypeDAL

Typing support for [PyDAL](http://web2py.com/books/default/chapter/29/6).
This package aims to improve the typing support for PyDAL. By using classes instead of the define_table method,
type hinting the result of queries can improve the experience while developing. In the background, the queries are still
generated and executed by pydal itself, this package only proves some logic to properly pass calls from class methods to
the underlying `db.define_table` pydal Tables.

### Translations from pydal to typedal

<table>
<tr>
<td>Description</td>
<td> pydal </td> <td> pydal alternative </td> <td> typedal </td> <td> typedal alternative(s) </td> <td> ... </td>
</tr>
<tr>
<tr>
<td>Setup</td>
<td>

```python
from pydal import DAL, Field

db = DAL(...)
```

</td>

<td></td>
<td>

```python
from typedal import TypeDAL, TypedTable, TypedField
from typing import Optional

db = TypeDAL(...)
```

</td>

</tr>
<tr>
<td>Table Definitions</td>
<td>

```python
db.define_table("table_name",
                Field("fieldname", "string", required=True),
                Field("otherfield", "float"))
```

</td>
<td>
</td>

<td>

```python
@db.define
class TableName(TypedTable):
    fieldname: str
    otherfield: Optional[float]
```

</td>

<td>

```python
class TableName(TypedTable):
    fieldname: str
    otherfield: float | None


db.define(TableName)
```

</td>
</tr>

<tr>
<td>Insert</td>

<td>

```python
db.table_name.insert(fieldname="value")
```

</td>

<td></td>

<td>

```python
db.table_name.insert(fieldname="value")
```
<td>

```python
TableName.insert(fieldname="value")
```

</td>
</tr>

<tr>
<td>(quick) Select</td>


<td>

```python
row = db.table_name(id=1)  # -> Any
```

</td>

<td></td>

<td>

```python
row: TableName = db.table_name(id=1)  # -> TableName
```
<td>

```python
row = TableName(id=1)  # -> TableName
```

</td>


</tr>

</table>


<!-- 
<td>

```python

```

</td>

<td></td>

<td>

<td>

```python

```

</td>
</tr>
-->