# darqos
# Copyright (C) 2026 David Arnold

# Tests for the operator's languages (darq.language, darq.host.locales).

import pytest

import darq
from darq import host, language


@pytest.mark.parametrize("environment, locales", [
    ({"LANG": "en_AU.UTF-8"}, ["en_AU.UTF-8"]),
    ({"LANGUAGE": "en_AU:de_CH", "LANG": "en_AU.UTF-8"}, ["en_AU", "de_CH"]),
    ({"LANGUAGE": "", "LANG": "fr_FR"}, ["fr_FR"]),
    ({"LANGUAGE": "en_AU::de_CH"}, ["en_AU", "de_CH"]),
    ({}, []),
])
def test_the_hosts_locales(environment, locales):
    assert host.locales(environment) == locales


@pytest.mark.parametrize("locales, chain", [
    (["en_AU.UTF-8"], ["en-AU", "en", "mul"]),
    (["en_AU", "de_CH"], ["en-AU", "en", "de-CH", "de", "mul"]),
    (["en_AU", "en", "de_CH"], ["en-AU", "en", "de-CH", "de", "mul"]),
    (["en_AU", "gsw", "de_CH"], ["en-AU", "en", "gsw", "de-CH", "de", "mul"]),
    (["fr_FR"], ["fr-FR", "fr", "en", "mul"]),
    (["sr_RS@latin"], ["sr-RS", "sr", "en", "mul"]),
    (["C.UTF-8"], ["en", "mul"]),
    (["POSIX"], ["en", "mul"]),
    ([], ["en", "mul"]),
])
def test_the_chain(locales, chain):
    assert language.chain(locales) == chain


def test_the_operators_languages(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "en_AU:de_CH")
    assert darq.languages() == ["en-AU", "en", "de-CH", "de", "mul"]
