# Standard Library (`std::*`)

Cilux ships a small standard library of common digital-logic building blocks, written entirely in Cilux
itself, at `lib/builtin/std/`. Because `lib/builtin` is part of the module resolution order used by
`import` (see [`ARCHITECTURE.md §3`](./ARCHITECTURE.md#3-module-system-internals-importexport) and
[`LANGUAGE_REFERENCE.md §13`](./LANGUAGE_REFERENCE.md#13-modules--import--namespaces)), every component
below is available from any Cilux program via `import std::<file>::<component>;` with no additional setup.

All components are purely **combinational** (no internal clocked state) unless noted otherwise, and every
multi-bit port (`a[3:0]`, etc.) is language-level sugar for scalar signals `a3, a2, a1, a0` — see the
[bus-sugar note](./LANGUAGE_REFERENCE.md#5-ranges-hilo--bus-sugar) for what that does and doesn't mean at
the synthesis level.

## `std::adder` (`adder.clx`)

Binary addition circuits.

| Component | Ports | Description |
|---|---|---|
| `half_adder` | `in: a, b` / `out: sum, cout` | 2-input addition, no carry-in. `sum = XOR(a,b)`, `cout = AND(a,b)`. |
| `full_adder` | `in: a, b, cin` / `out: sum, cout` | Built from two `half_adder`s: `sum` and carry-out `OR`ed from both stages. |
| `ripple_carry_adder_4bit` | `in: a[3:0], b[3:0], cin` / `out: sum[3:0], cout` | Four `full_adder` instances chained via each stage's `cout` feeding the next stage's `cin`. |

```cilux
import std::adder::half_adder;

half_adder ha;
r = ha(1, 0);
print(r.sum, r.cout);   // 1 0
```

## `std::mux` (`mux.clx`)

Multiplexers.

| Component | Ports | Description |
|---|---|---|
| `mux_2` | `in: d0, d1, sel` / `out: out` | 2-to-1 mux: `out = OR(AND(NOT(sel), d0), AND(sel, d1))`. |
| `mux_4` | `in: d0..d3, s0, s1` / `out: out` | Built from three `mux_2`s (two low-stage muxes selected on `s0`, combined by a final mux selected on `s1`). |
| `mux_8` | `in: d0..d7, s0, s1, s2` / `out: out` | Built from two `mux_4`s + one `mux_2`, selected by `s0`,`s1` (low/high) and `s2` (final select). |

```cilux
import std::mux::mux_2;

r = mux_2(d0=1, d1=0, sel=1);
print(r.out)   // 0
```

## `std::alu` (`alu.clx`)

A minimal Arithmetic Logic Unit built directly on top of `std::mux` (imported internally via a relative,
same-directory import: `import mux::{mux_2};`).

| Component | Ports | Description |
|---|---|---|
| `alu_1bit` | `in: a, b, op[1:0]` / `out: result, cout` | Computes `XOR` (sum), `AND`, `OR`, `XOR` in parallel, then selects one via two chained `mux_2`s keyed on `op0`/`op1`. `cout` is always `AND(a,b)` (the arithmetic carry from the sum path). |
| `alu_4bit` | `in: a[3:0], b[3:0], op[1:0]` / `out: result[3:0], cout` | Four `alu_1bit` slices sharing the same `op` select lines; `cout` is taken from the MSB slice (`alu3`). |

**Operation encoding (`op[1:0]`)**:

| `op1` | `op0` | Operation |
|---|---|---|
| 0 | 0 | `ADD` (bitwise sum, no inter-bit carry chaining — see note below) |
| 0 | 1 | `AND` |
| 1 | 0 | `OR` |
| 1 | 1 | `XOR` |

```cilux
import std::alu::alu_1bit;

r = alu_1bit(a=1, b=1, op0=0, op1=0);
print(r.result, r.cout)  // 0 1
```

> **Note:** `alu_4bit`'s `ADD` operation applies each bit-slice's `alu_1bit` independently and does **not**
> chain carries between slices (unlike `ripple_carry_adder_4bit`, which does) — it reuses the per-bit XOR/AND
> combinational paths of `alu_1bit`, not a true 4-bit ripple-carry adder. For real 4-bit addition, use
> `std::adder::ripple_carry_adder_4bit`.

## `std::comparator` (`comparator.clx`)

Binary magnitude comparators.

| Component | Ports | Description |
|---|---|---|
| `comparator_1bit` | `in: a, b` / `out: eq, gt` | `eq = XNOR(a,b)`, `gt = AND(a, NOT(b))`. |
| `comparator_4bit` | `in: a[3:0], b[3:0]` / `out: eq, gt, lt` | MSB-first priority comparison across four `comparator_1bit` slices; `eq` is the `AND` of all four per-bit equalities; `gt`/`lt` resolve from the highest-order bit where `a` and `b` first differ. |

```cilux
import std::comparator::comparator_1bit;

r = comparator_1bit(a=1, b=0);
print(r.eq, r.gt)   // 0 1
```

## `std::decoder` (`decoder.clx`)

Binary decoders (one-hot output selection with an enable line).

| Component | Ports | Description |
|---|---|---|
| `decoder_1to2` | `in: in, en` / `out: y[1:0]` | `y1 = AND(in, en)`, `y0 = AND(NOT(in), en)`. |
| `decoder_2to4` | `in: in[1:0], en` / `out: y[3:0]` | Two `decoder_1to2`s gated by `in1` to select the low/high output pair. |
| `decoder_3to8` | `in: in[2:0], en` / `out: y[7:0]` | Two `decoder_2to4`s gated by `in2` to select the low/high nibble. |

```cilux
import std::decoder::decoder_2to4;

r = decoder_2to4(in0=1, in1=0, en=1);
print(r.y0, r.y1, r.y2, r.y3)   // 0 0 1 0
```

## `std::encoder` (`encoder.clx`)

Binary encoders (inverse of a decoder — one-hot input to binary index output).

| Component | Ports | Description |
|---|---|---|
| `encoder_4to2` | `in: d[3:0]` / `out: y[1:0]` | Plain (non-priority) 4-to-2 encoder; assumes exactly one input line is asserted. |
| `encoder_8to3` | `in: d[7:0]` / `out: y[2:0]` | Plain 8-to-3 encoder, same one-hot-input assumption. |
| `priority_encoder_4to2` | `in: d[3:0]` / `out: y[1:0], valid` | 4-to-2 **priority** encoder — correctly resolves the highest-indexed asserted input when more than one `d` line is high; `valid` indicates any input was asserted at all. |

```cilux
import std::encoder::priority_encoder_4to2;

r = priority_encoder_4to2(d0=1, d1=1, d2=0, d3=0);
print(r.y0, r.y1, r.valid)   // 1 0 1  (d1 wins priority over d0)
```

## `std::converter` (`converter.clx`)

Binary ⇄ Gray code converters.

| Component | Ports | Description |
|---|---|---|
| `bin_to_gray_4bit` | `in: b[3:0]` / `out: g[3:0]` | `g3=b3`, then each lower Gray bit is the `XOR` of adjacent binary bits (`g[i] = b[i] XOR b[i+1]`). |
| `gray_to_bin_4bit` | `in: g[3:0]` / `out: b[3:0]` | `b3=g3`, then each lower binary bit is the `XOR` of the current Gray bit with the *already-computed* next-higher binary bit (`b[i] = g[i] XOR b[i+1]`) — the standard sequential-XOR Gray-to-binary decode, expressed here as a purely combinational chain since all four output bits are derived in one pass from already-declared upstream wires.

```cilux
import std::converter::bin_to_gray_4bit;

r = bin_to_gray_4bit(b0=1, b1=0, b2=0, b3=0);
print(r.g0, r.g1, r.g2, r.g3)   // 1 1 1 1
```

## Importing Multiple Components / Whole Modules

```cilux
// Import everything from one file:
import std::adder::*;

// Import a whole namespace and address members via `::`:
import std::mux;
mux_2_result = mux::mux_2(...); // conceptually; typically you'd import the symbol directly instead

// Selective import with aliasing:
import std::comparator::{comparator_1bit as cmp1, comparator_4bit as cmp4};
```

See [`LANGUAGE_REFERENCE.md §13`](./LANGUAGE_REFERENCE.md#13-modules--import--namespaces) for the full
import grammar and resolution rules, and [`ARCHITECTURE.md §3`](./ARCHITECTURE.md#3-module-system-internals-importexport)
for exactly how directory-modules like `std` itself are auto-constructed from their sibling `.clx` files.
