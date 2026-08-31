# @DefaultComponent Reference

**Applies to:** Kora 2.x (`io.koraframework`)

## Contents

- [Overview](#overview)
- [Resolution Precedence](#resolution-precedence)
- [Library Default + Application Override](#library-default--application-override)
- [Overriding by Signature with @Override](#overriding-by-signature-with-override)
- [@DefaultComponent on a Class](#defaultcomponent-on-a-class)
- [Interaction with @Tag](#interaction-with-tag)
- [Interaction with All&lt;T&gt;](#interaction-with-allt)
- [Common Mistakes](#common-mistakes)
- [Related References](#related-references)

## Overview

`@DefaultComponent` (`io.koraframework.common.annotation.DefaultComponent`) marks a provider as a
**fallback**: it is used only when nothing else of that type and tag is available. It is how a
library ships a working default that an application can replace without forking the module.

Its `@Target` is `{METHOD, TYPE}` — it works on a module provider method **and** on a `@Component`
class.

## Resolution Precedence

When the processor resolves one dependency claim it collects every non-template declaration whose
type and tag match, then:

1. **exactly one candidate** → use it, `@DefaultComponent` or not;
2. **more than one** → discard every `@DefaultComponent` candidate;
   - exactly one non-default remains → use it;
   - still ambiguous → unless *all* remaining candidates are `@Conditional`, fail with
     `Multiple components match dependency`.

So the ordering is simply: **any non-default provider beats every `@DefaultComponent` provider**, and
two non-default providers of the same type+tag are an error. `@DefaultComponent` never wins a fight
with a plain provider, and never breaks a tie with another `@DefaultComponent` — give exactly one of
them a distinct `@Tag`, or drop `@DefaultComponent` from the one that should win.

## Library Default + Application Override

The library declares the default:

```java
package com.example.sms;

import io.koraframework.common.annotation.DefaultComponent;

public interface SmsCellularModule {

    @DefaultComponent
    default SmsCellularProvider smsCellularProvider() {
        return () -> "1";
    }
}
```

The application supplies its own provider of the same type; nothing else is required:

```java
@KoraApp
public interface Application extends HoconConfigModule, SmsCellularModule {

    static void main(String[] args) { KoraApplication.run(ApplicationGraph::graph); }

    default SmsCellularProvider smsCellularProvider(SmsConfig config) {
        return config::countryCode;
    }
}
```

Note that the override is free to take different parameters — precedence is decided by type and tag,
not by signature.

## Overriding by Signature with `@Override`

When the override has the **same signature** as the default, mark it `@Override` (Java) /
`override` (Kotlin). The processor resolves the overridden method and removes that declaration from
the graph entirely, rather than relying on precedence:

```java
@KoraApp
public interface Application extends HoconConfigModule, LogbackModule, EmailModule {

    static void main(String[] args) { KoraApplication.run(ApplicationGraph::graph); }

    @Tag(EmailModule.EmailTag.class)
    @Override
    default Supplier<String> emailNotifierHeaderSupplier() {
        return () -> "[EMAIL OVERRIDDEN] ";
    }
}
```

```kotlin
@KoraApp
interface Application : HoconConfigModule, LogbackModule, EmailModule {

    @Tag(EmailModule.EmailTag::class)
    override fun emailNotifierHeaderSupplier(): Supplier<String> = Supplier { "[EMAIL OVERRIDDEN] " }
}
```

Two things to keep in mind:

- **Re-declare the `@Tag`.** Tags are read from the method you compile, so an override that omits it
  publishes the component untagged and the tagged claim then finds nothing.
- `@Override` is only usable when the signature matches. For a different signature, rely on
  precedence as shown in the previous section.

## `@DefaultComponent` on a Class

```java
@DefaultComponent
@Component
public final class InMemoryEventStore implements EventStore { }

// an application-supplied provider wins
@Module
public interface EventStoreModule {
    default EventStore eventStore(JdbcConnectionFactory factory) {
        return new JdbcEventStore(factory);
    }
}
```

This is a real 2.0 capability: the processor reads `@DefaultComponent` off the class element as well
as off provider methods.

## Interaction with `@Tag`

Precedence is evaluated **per (type, tag) pair**. A `@DefaultComponent` under `@Tag(A.class)` and a
plain provider under `@Tag(B.class)` never compete — they are different claims.

```java
@Module
public interface CacheModule {

    @Tag(L1.class) @DefaultComponent
    default Cache l1() { return new CaffeineCache(); }

    @Tag(L2.class) @DefaultComponent
    default Cache l2() { return new NoopCache(); }
}

// replaces only L2
@KoraApp
public interface Application extends CacheModule {
    @Tag(L2.class)
    default Cache redisL2(RedisClient client) { return new RedisCache(client); }
}
```

## Interaction with `All<T>`

`All<T>` collects matching components, but a `@DefaultComponent` is skipped when other candidates
exist — the point of a default is to be unnecessary once a real implementation is present. With no
other candidate, the default is collected like anything else.

## Common Mistakes

### Importing from the wrong package

```java
// BAD
import io.koraframework.common.DefaultComponent;

// GOOD
import io.koraframework.common.annotation.DefaultComponent;
```

### Two `@DefaultComponent` providers for the same type and tag

```java
// BAD — neither can win by precedence
@Module interface A { @DefaultComponent default Logger logger() { return new LoggerA(); } }
@Module interface B { @DefaultComponent default Logger logger() { return new LoggerB(); } }

// GOOD — exactly one default; the other is a plain provider or carries a distinct @Tag
@Module interface A { @DefaultComponent default Logger logger() { return new LoggerA(); } }
@Module interface B { default Logger logger() { return new LoggerB(); } }
```

### Assuming `@DefaultComponent` is only for modules

It is valid on a `@Component` class too — see above. What it is *not* valid on is a constructor
parameter or a field.

### Override module not connected

```java
// BAD — the override lives in a module nobody extends and nothing compiles it into the graph
// (a library module in another Gradle subproject)
@KoraApp
public interface Application extends LibraryModule { }

// GOOD
@KoraApp
public interface Application extends LibraryModule, CustomLoggerModule { }
```

A `@Module` compiled in the same Gradle module needs no `extends` — see
[Module Auto-Discovery Reference](module-auto-discovery-reference.md).

### Dropping the tag when overriding

```java
// BAD — publishes an untagged component; the @Tag(EmailTag.class) claim still finds only the default
@Override
default Supplier<String> emailNotifierHeaderSupplier() { return () -> "…"; }

// GOOD
@Tag(EmailModule.EmailTag.class)
@Override
default Supplier<String> emailNotifierHeaderSupplier() { return () -> "…"; }
```

## Related References

- [Component Registration Reference](component-registration-reference.md) — the full resolution order
- [Component Factories Reference](component-factories-reference.md) — provider forms
- [Tags & Collections Reference](tags-collections-reference.md) — how tag matching works
- [Conditional Components Reference](conditional-components-reference.md) — gating instead of defaulting
