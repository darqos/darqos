#! usr/bin/env python
# darqos
# Copyright (C) 2022 David Arnold

# Try Lark

from lark import Lark, Transformer

# * expression: bracketed_expression | expression operator expression
#               | value | unary_sign value
# * unary_sign: '+' | '-'
# * value: integer | float | sci_float
# * bracketed_expression: '(' expression ')'
# * operator: '+' | '-' | '*' | '/' | '%'
#
# * Numbers:
#  * base_number: digit+ | digit+ '.' | '.' digit+ | digit+ '.' digit+
#  * number: [unary_operator] base_number [ ('e' | 'E') [unary_sign] digit+ ]

# binary_operator: "+" | "-" | "*" | "/" | "mod" | "%"


class NumberTransformer(Transformer):

    def integer(self, tokens):
        return int(tokens[0])

    def whole_real(self, tokens):
        return float(tokens[0])

    def fractional_real(self, tokens):
        return float(tokens[0].value + tokens[1].value)

    def real(self, tokens):
        return float(tokens[0].value + tokens[1].value + tokens[2].value)

    def simple_number(self, tokens):
        return tokens[0]

    def unary_operator(self, tokens):
        return tokens[0].value

    def signed_simple(self, tokens):
        if tokens[0] == '-':
            return tokens[1] * -1
        else:
            return tokens[1]

    def exp(self, tokens):
        return tokens[0].value

    def signed_exponent(self, tokens):
        return int(tokens[0] + tokens[1].value)

    def exponent_value(self, tokens):
        return int(tokens[0])

    def lay_number(self, tokens):
        return tokens[0]

    def sci_number(self, tokens):
        print("sci_number", tokens)
        return tokens[0] * pow(10, tokens[2])

    def number(self, tokens):
        return tokens[0]

parser = Lark(r"""

DIGITS: ( "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" )+
DOT: "."
UNARY_OPERATOR: "+" | "-"
EXP: "e" | "E"


unary_operator: UNARY_OPERATOR

exp: EXP

integer: DIGITS
whole_real: DIGITS DOT
fractional_real: DOT DIGITS
real: DIGITS DOT DIGITS
simple_number: integer | whole_real | fractional_real | real

signed_simple: unary_operator simple_number

signed_exponent: unary_operator DIGITS
exponent_value: DIGITS | signed_exponent

lay_number: simple_number | signed_simple
sci_number: lay_number exp exponent_value

number: lay_number | sci_number
 
%import common.WS
%ignore WS 
""", start="number")


tests = [
    "1",
    ".1",
    "1.",
    "1.0",
    "-1",
    "-.1",
    "-1.",
    "-1.1",
    "+1",
    "+.1",
    "+1.",
    "+1.1",
    "1234567890",
    "12345.67890",
    "+12345.67890",
    "-1.234567890e20",
    "-1.234567890e-20",
]

for test in tests:
    result = parser.parse(test)
    print(f"{test} -> {result.pretty()}")

    transform = NumberTransformer().transform(result)
    print(transform)
