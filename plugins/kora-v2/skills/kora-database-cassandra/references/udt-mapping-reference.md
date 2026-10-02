# UDT Mapping Reference

**Artifact:** `io.koraframework:database-cassandra`
**Annotation:** `io.koraframework.database.cassandra.annotation.UDT`
**Driver types:** `com.datastax.oss.driver.api.core.data.UdtValue`,
`com.datastax.oss.driver.api.core.type.UserDefinedType`

## Contents

- [What @UDT generates](#what-udt-generates)
- [The type name does not come from the class](#the-type-name-does-not-come-from-the-class)
- [Declaring a UDT](#declaring-a-udt)
- [Nested UDTs](#nested-udts)
- [Collections of UDTs](#collections-of-udts)
- [Kotlin](#kotlin)
- [Hand-written UDT mappers](#hand-written-udt-mappers)
- [Pitfalls](#pitfalls)

---

## What @UDT generates

`@UDT` may be placed on a **record or a Java-bean-like class** (`@UDT can be used only on records
and Java bean-like classes.`). For each annotated type the processor emits four mappers:

| Generated type | Contract | Used for |
|---|---|---|
| `$Address_CassandraRowColumnMapper` | `CassandraRowColumnMapper<Address>` | reading a UDT column |
| `$Address_List_CassandraRowColumnMapper` | `CassandraRowColumnMapper<List<Address>>` | reading a `LIST<FROZEN<…>>` column |
| `$Address_CassandraParameterColumnMapper` | `CassandraParameterColumnMapper<Address>` | writing a UDT column |
| `$Address_List_CassandraParameterColumnMapper` | `CassandraParameterColumnMapper<List<Address>>` | writing a `LIST<FROZEN<…>>` column |

The Kora extension hands these to the graph automatically when a repository or an entity mapper
needs `CassandraRowColumnMapper<Address>` / `<List<Address>>` or the parameter equivalents — you
never reference the generated names. Names carry a `$` prefix and every enclosing type, so a UDT
declared as `UserRepository.Entity.Name` generates
`$UserRepository_Entity_Name_CassandraRowColumnMapper`.

Field names inside the UDT follow the same rules as entity columns: `snake_case` of the component
name by default, `@Column("…")` to override, `@NamingStrategy` to change the converter.

---

## The type name does not come from the class

`@UDT` has **no attributes** — there is no `@UDT("address")`. The generated write mapper starts
with:

```java
var _type = (UserDefinedType) _stmt.getType(_index);
var _index_of_first = _type.firstIndexOf("first");
var _object = _type.newValue();
```

It takes the user-defined type off the **bound statement's own metadata** and addresses fields by
name. The read mapper does the same against the `UdtValue` in the row. Consequences:

- The CQL type may be named anything; the Java class name is irrelevant. The migrated Kora example
  maps a record called `Entity.Name` onto a CQL type called `username`.
- Only **field names** must line up. A renamed CQL field surfaces at runtime, not at compile time.
- The Java type of each field must be readable through the driver's codec for the declared CQL
  field type.

```sql
CREATE TYPE IF NOT EXISTS username(first text, last text);
CREATE TABLE IF NOT EXISTS entities_udt (id VARCHAR, name FROZEN<username>, PRIMARY KEY (id));
```

```java
@Repository
public interface UserRepository extends CassandraRepository {

    @EntityCassandra
    record Entity(String id, Name name) {

        @UDT
        record Name(String first, String last) {}
    }

    @Query("SELECT * FROM entities_udt WHERE id = :id")
    @Nullable
    Entity findById(String id);

    @Query("INSERT INTO entities_udt(id, name) VALUES (:entity.id, :entity.name)")
    void insert(Entity entity);
}
```

---

## Declaring a UDT

Nested inside the repository or the owning entity (the shape the Kora example uses), or as a
standalone type — both work identically:

```java
@UDT
public record Address(
    @Column("street") String street,
    @Column("city") String city,
    @Column("zip_code") String zipCode
) {}

@EntityCassandra
@Table("users")
public record User(
    @Id String id,
    String email,
    Address address
) {}
```

```sql
CREATE TYPE IF NOT EXISTS address (street TEXT, city TEXT, zip_code TEXT);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT, address FROZEN<address>);
```

An entity that only *contains* a UDT needs no extra annotation: `@EntityCassandra` on the owner
plus `@UDT` on the nested type is enough for both directions.

---

## Nested UDTs

A `@UDT` may contain another `@UDT`, to any depth — the framework's own UDT test compiles a record
with a nested `@UDT` component and round-trips it.

```java
@UDT
public record Coordinates(double latitude, double longitude) {}

@UDT
public record Location(String name, Coordinates coordinates) {}

@EntityCassandra
@Table("stores")
public record Store(@Id String id, Location location) {}
```

```sql
CREATE TYPE IF NOT EXISTS coordinates (latitude DOUBLE, longitude DOUBLE);
CREATE TYPE IF NOT EXISTS location (name TEXT, coordinates FROZEN<coordinates>);
CREATE TABLE IF NOT EXISTS stores (id TEXT PRIMARY KEY, location FROZEN<location>);
```

Every level must carry `@UDT`; an un-annotated nested record fails with
`No component found for dependency: CassandraRowColumnMapper<Coordinates>`.

---

## Collections of UDTs

**`List<UDT>` is generated. `Set<UDT>` and `Map<K, UDT>` are not.**

The UDT processor emits exactly two collection mappers — the `List_` pair above — and the Kora
extension only rewrites `java.util.List` element types. There is no `Set` or `Map` branch, so:

```java
@EntityCassandra
@Table("users")
public record User(
    @Id String id,
    Address address,              // OK — $Address_Cassandra*ColumnMapper
    List<Address> addresses       // OK — $Address_List_Cassandra*ColumnMapper
) {}
```

```sql
CREATE TABLE IF NOT EXISTS users (
    id        TEXT PRIMARY KEY,
    address   FROZEN<address>,
    addresses LIST<FROZEN<address>>
);
```

A `Set<Address>` or `Map<String, Address>` component fails the graph with
`No component found for dependency: CassandraRowColumnMapper<Set<Address>>`. Either model the
column as a `LIST<FROZEN<…>>` and keep `List<Address>` on the Java side, or write the mapper pair
yourself (see below) — `SettableByName#setSet` / `#setMap` and `GettableByName#getSet` / `#getMap`
with `UdtValue.class` as the element type are the driver calls you need.

---

## Kotlin

```kotlin
@EntityCassandra
data class UdtEntity(val id: String, val name: Name) {

    @UDT
    data class Name(val first: String, val last: String)
}
```

With explicit column names, use the `@field:` use-site target — KSP reads annotations off the
property declaration:

```kotlin
@UDT
data class Address(
    @field:Column("street") val street: String,
    @field:Column("city") val city: String,
    @field:Column("zip_code") val zipCode: String
)

@EntityCassandra
@Table("users")
data class User(
    @field:Id val id: String,
    val address: Address,
    val addresses: List<Address>
)
```

Nullable UDT columns are `Address?` in Kotlin and `@Nullable Address` (JSpecify, type-use) in Java.
The generated write mapper calls `_stmt.setToNull(index)` for a null value, and the generated read
mapper throws `NullPointerException: Result field … is not nullable but row … has null` when a
non-nullable component reads null.

---

## Hand-written UDT mappers

Needed for `Set<UDT>` / `Map<K, UDT>`, for a UDT you cannot annotate (a third-party type), or when
the CQL field names cannot be matched by naming strategy alone.

```java
@Component
public final class AddressReadMapper implements CassandraRowColumnMapper<Address> {
    @Override
    public @Nullable Address apply(GettableByName row, int index) {
        var udt = row.getUdtValue(index);
        if (udt == null) return null;
        return new Address(udt.getString("street"), udt.getString("city"), udt.getString("zip_code"));
    }
}

@Component
public final class AddressWriteMapper implements CassandraParameterColumnMapper<Address> {
    @Override
    public void apply(SettableByName<?> stmt, int index, @Nullable Address value) {
        if (value == null) {
            stmt.setToNull(index);
            return;
        }
        var type = (UserDefinedType) stmt.getType(index);
        var udt = type.newValue()
            .setString("street", value.street())
            .setString("city", value.city())
            .setString("zip_code", value.zipCode());
        stmt.setUdtValue(index, udt);
    }
}
```

Attach them with `@Mapping` on the entity component (both directions on the same component) or on
the query parameter for the write side only. Both classes above are `final` with a no-arg
constructor, so the generated repository would construct them itself; `@Component` matters when the
mapper takes constructor dependencies or is not `final` — see
[CQL Repository Reference](cql-repository-reference.md#when-a-mapper-must-be-a-component).

In Kotlin the write mapper's `value` parameter must be declared `Address?`, or the compiler reports
`'apply' overrides nothing`.

---

## Pitfalls

| Symptom | Cause / fix |
|---|---|
| `No component found for dependency: CassandraRowColumnMapper<X>` | `X` is missing `@UDT`, or is a `Set`/`Map` of a UDT |
| `No component found for dependency: CassandraRowMapper<X>` | the **owning** entity is missing `@EntityCassandra` |
| `@UDT can be used only on records and Java bean-like classes.` | `@UDT` on an interface, enum or annotation type |
| `InvalidQueryException` mentioning a non-frozen UDT | CQL needs `FROZEN<…>` for a UDT column and `LIST<FROZEN<…>>` for a list of them |
| Field silently null after a schema change | `@UDT` matches by field **name** off the statement metadata — a renamed CQL field is not a compile error |
| `IllegalArgumentException` / `ClassCastException` on `getType(index)` | the target column is not a user-defined type (or not a list of one) in the live schema |

---

## See Also

- [CQL Repository Reference](cql-repository-reference.md)
- [Cassandra Config Reference](cassandra-config-reference.md)
