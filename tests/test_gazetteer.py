"""Tests for the curated skill gazetteer."""

from __future__ import annotations

import re

import pytest

from backend.matching.gazetteer import (
    GAZETTEER_VERSION,
    SKILLS_PATH,
    Gazetteer,
    Mention,
    file_version,
    fold,
)


@pytest.fixture(scope="module")
def g() -> Gazetteer:
    return Gazetteer.load()


def test_fold_strips_accents_and_lowercases():
    assert fold("Élève Ingénieur À LYON") == "eleve ingenieur a lyon"
    assert fold("été") == "ete"  # decomposed input folds the same


def test_short_names_need_exact_case_and_boundaries(g):  # Review Focus 3
    assert g.skills("Poste en CDI, équipe R&D, on-going projects") == set()
    assert {"go", "r", "c"} <= {s.lower() for s in g.skills("We use Go, R and C daily")}


def test_short_names_ignore_lowercase_elisions_and_longer_words(g):
    text = "C'est un poste R & D. let's go, we are going to recruit; c r"
    assert g.skills(text) == set()
    assert g.skills("Cœur de métier : CDI, Rome, Gold") == set()


@pytest.mark.parametrize(
    "text",
    [
        "Go-to-Market Manager",
        "Head of Go to Market",
        "Go Live support and Go-Live planning",
        "Go/No-Go decision, a No Go",
        "Go deep on customer problems",
        "C-level stakeholders and the C-suite",
        "Series C startup, Série C",
        "G&C engineer, C&I segment",
        "Objective-C",
        "Catégorie C",
        "Niveau C1; C. Martin",
        "R. Dupont",
        "note R",
        "R-squared",
    ],
)
def test_business_jargon_is_not_a_language(g, text):  # real strings from scraped postings
    assert g.skills(text) == set()


def test_short_names_still_match_next_to_punctuation(g):
    assert g.skills("Backend Engineer (Go)") == {"go"}
    assert g.skills("Go, Rust; embedded C. R and Python.") == {"go", "rust", "c", "r", "python"}
    assert g.skills("Langages : C, Go") == {"c", "go"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Construire et maintenir une vue unifiée", set()),
        ("une vue d'ensemble, en vue de, point de vue", set()),
        ("Develop a ROS node in C++", {"ros", "c++"}),
        ("multi-node training clusters, multi-GPU/multi-node", set()),
        ("You excel at communication", set()),
        ("Tableau de bord, tableaux de bord", set()),
        ("Spring 2026 internship", set()),
        ("Shell is an energy company", set()),
        ("50 mL", set()),
        ("at the helm of a team", set()),
        ("Elk kwartaal", set()),
        ("Vue.js, vuejs, Node.js, NodeJS", {"vue.js", "node.js"}),
        (
            "Excel, Tableau, Spring, Helm, ELK, ML",
            set("excel tableau helm elk".split()) | {"spring boot", "machine learning"},
        ),
        ("shell scripting, scripts shell", {"bash"}),
    ],
)
def test_ordinary_words_are_not_skills(g, text, expected):
    assert g.skills(text) == expected


@pytest.mark.parametrize(
    ("text", "skill"),
    [
        ("INGENIEUR ROBOTICIEN", "robotics"),
        ("Ingénieur automaticien", "plc"),
        ("Technicien automatisme", "plc"),
    ],
)
def test_french_profession_names(g, text, skill):
    assert g.skills(text) == {skill}


def test_c_family_tokens_are_distinct(g):
    assert g.skills("C/C++ et C# sur cible embarquée") == {"c", "c++", "c#"}
    assert g.skills("Développement .NET") == {"c#"}


def test_aliases_fold_accents_and_map_to_canonical(g):
    assert "ros 2" in g.skills("Expérience avec ROS2 / ros 2 Humble")
    assert g.skills("ROS2") == {"ros 2"}
    assert g.skills("ROS Noetic") == {"ros"}
    assert g.skills("Intégration continue et pipelines de données") == {"ci/cd", "etl"}
    assert g.skills("INTEGRATION CONTINUE") == {"ci/cd"}


def test_matching_is_on_word_boundaries_never_substring(g):
    assert g.skills("javascripting dockerized pythonic gitlabs rosette") == set()
    assert g.skills("(Python), docker; Kubernetes.") == {"python", "docker", "kubernetes"}


def test_longest_alias_wins(g):
    assert g.skills("Spring Boot and Apache Kafka") == {"spring boot", "kafka"}
    assert g.skills("embedded Linux") == {"embedded linux"}


def test_extract_returns_spans_in_the_original_text(g):
    text = "Maîtrise d'Apprentissage Automatique, de ROS 2 et de C++."
    mentions = g.extract(text)
    assert all(isinstance(m, Mention) for m in mentions)
    assert [(m.skill, text[m.start : m.end]) for m in mentions] == [
        ("machine learning", "Apprentissage Automatique"),
        ("ros 2", "ROS 2"),
        ("c++", "C++"),
    ]


def test_extract_spans_survive_decomposed_accents(g):
    text = "déploiement continu avec Docker"
    assert [(m.skill, text[m.start : m.end]) for m in g.extract(text)] == [
        ("ci/cd", "déploiement continu"),
        ("docker", "Docker"),
    ]


def test_extract_keeps_every_mention_in_order(g):
    mentions = g.extract("Python, then Docker, then python again")
    assert [m.skill for m in mentions] == ["python", "docker", "python"]
    assert [m.start for m in mentions] == sorted(m.start for m in mentions)


def test_empty_text(g):
    assert g.extract("") == []
    assert g.skills("   \n") == set()


@pytest.mark.parametrize(
    ("text", "skill"),
    [
        ("MoveIt 2 and Gazebo simulation", "moveit"),
        ("MoveIt 2 and Gazebo simulation", "gazebo"),
        ("NVIDIA Isaac Sim", "isaac sim"),
        ("modèles URDF", "urdf"),
        ("SLAM lidar", "slam"),
        ("planification de trajectoire", "path planning"),
        ("cinématique inverse", "kinematics"),
        ("commande d'axes", "motion control"),
        ("FreeRTOS sur STM32", "freertos"),
        ("FreeRTOS sur STM32", "stm32"),
        ("un RTOS", "rtos"),
        ("bus CAN", "can bus"),
        ("Linux embarqué avec Yocto", "embedded linux"),
        ("Linux embarqué avec Yocto", "yocto"),
        ("programmation d'automates programmables", "plc"),
        ("Siemens TIA Portal", "siemens tia portal"),
        ("Modbus TCP, EtherCAT, PROFINET", "modbus"),
        ("Modbus TCP, EtherCAT, PROFINET", "ethercat"),
        ("Modbus TCP, EtherCAT, PROFINET", "profinet"),
        ("serveur OPC-UA", "opc ua"),
        ("capteurs IO-Link", "io-link"),
        ("supervision SCADA", "scada"),
        ("robots collaboratifs", "cobot"),
        ("OpenCV, PyTorch, TensorFlow", "opencv"),
        ("OpenCV, PyTorch, TensorFlow", "pytorch"),
        ("OpenCV, PyTorch, TensorFlow", "tensorflow"),
        ("nuages de points avec PCL", "point cloud"),
        ("nuages de points avec PCL", "pcl"),
        ("SolidWorks, MATLAB/Simulink", "solidworks"),
        ("SolidWorks, MATLAB/Simulink", "matlab"),
        ("SolidWorks, MATLAB/Simulink", "simulink"),
    ],
)
def test_robotics_and_industrial_families_are_covered(g, text, skill):
    assert skill in g.skills(text)


def test_automate_as_an_english_verb_is_not_a_plc(g):
    assert "plc" not in g.skills("You will automate deployments; she automates tests")


def test_owner_keywords_become_skills():  # Review Focus 4
    g = Gazetteer.load(extra_terms=["Soudure TIG"])
    assert "soudure tig" in g.skills("maîtrise de la soudure TIG exigée")
    assert "soudure tig" not in Gazetteer.load().skills("maîtrise de la soudure TIG exigée")


def test_owner_keyword_that_is_already_an_alias_keeps_its_canonical():
    g = Gazetteer.load(extra_terms=["ROS2", "k8s", "go", "  ", "Ingénieur Robotique"])
    assert g.skills("ROS2 on k8s") == {"ros 2", "kubernetes"}
    # "go" is the case-sensitive alias of Go: the keyword must not loosen it.
    assert g.skills("let's go") == set()
    # A job-title keyword that contains a curated skill is not a skill of its own.
    assert g.skills("poste d'ingenieur robotique") == {"robotics"}


def test_owner_keyword_does_not_shadow_the_curated_skill_inside_it():
    plain = Gazetteer.load()
    g = Gazetteer.load(extra_terms=["Ingénieur Robotique", "Python Developer", "Soudure TIG"])
    for text in ("Ingénieur robotique. Python, C++", "Python Developer", "Senior Python developer"):
        assert g.skills(text) == plain.skills(text), text
    assert g.skills("Python developer, soudure TIG") == {"python", "soudure tig"}


def test_owner_keywords_do_not_shadow_each_other():
    g = Gazetteer.load(extra_terms=["soudure", "soudure TIG"])
    assert g.skills("soudure TIG") == {"soudure tig"}
    assert g.skills("soudure MIG") == {"soudure"}


def test_owner_keyword_longer_than_any_curated_alias():
    term = "responsable de la maintenance des lignes de production automatisées"
    assert Gazetteer.load(extra_terms=[term]).skills(f"Poste : {term}.") == {term}


def test_related_same_family_only(g):
    assert g.related("ros", "moveit")
    assert g.related("postgresql", "mysql")
    assert not g.related("ros", "postgresql")
    assert not g.related("ros", "not a skill")
    assert not g.related("not a skill", "neither")
    # Families marked "!" (languages, OS...) give no credit between members.
    assert not g.related("python", "java")
    extra = Gazetteer.load(extra_terms=["Soudure TIG", "Soudure MIG"])
    assert not extra.related("soudure tig", "soudure mig")


def test_load_parses_families_aliases_and_case_markers(tmp_path):
    path = tmp_path / "skills.txt"
    path.write_text(
        "# comment\n\n@fam\nAlpha | alfa | !AL\nbeta\n@solo!\ngamma\ndelta\n", encoding="utf-8"
    )
    g = Gazetteer.load(path=path)
    assert g.skills("ALFA and Beta, AL but not al") == {"alpha", "beta"}
    assert g.skills("al") == set()
    assert g.related("alpha", "beta")
    assert not g.related("gamma", "delta")
    assert not g.related("alpha", "gamma")


def test_version_changes_with_file(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("python\n", encoding="utf-8")
    b.write_text("python\njava\n", encoding="utf-8")
    assert file_version(a) != file_version(b)
    assert file_version(a) == file_version(a)
    assert re.fullmatch(r"[0-9a-f]{12}", file_version(a))
    assert GAZETTEER_VERSION == file_version(SKILLS_PATH)


def test_curated_file_has_no_alias_claimed_by_two_skills():
    """A duplicate alias silently resolves to the first skill; keep the list unambiguous."""
    seen: dict[tuple[bool, str], str] = {}
    for line in SKILLS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "@")):
            continue
        parts = [p.strip() for p in line.split("|")]
        canon = parts[0].lstrip("!").lower()
        for alias in parts:
            key = (alias.startswith("!"), fold(alias.lstrip("!")))
            assert seen.setdefault(key, canon) == canon, f"{alias!r} is listed twice"
