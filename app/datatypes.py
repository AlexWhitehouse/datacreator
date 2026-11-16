"""Data types: the built-in library, custom user-defined kinds, and per-set generation context.

A *set* is one synthetic record. Every document in a set (one per selected template) reads
from the same SetContext, so fields with the same name share a value across templates and
record-based built-ins (name, DOB, address...) agree with each other.
"""

from __future__ import annotations

import random
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from faker import Faker

from . import db, uk_data

VALUE_KINDS = ("text", "date", "bool", "number")
CUSTOM_KINDS = ("picklist", "pattern", "number_range", "date_range", "composite", "boolean", "ai")


class _AiPending:
    """Placeholder for a live AI field; filled in by the run engine after the AI call."""

    def __repr__(self):
        return "<AI pending>"


AI_PENDING = _AiPending()

_faker_local = threading.local()


def _faker() -> Faker:
    f = getattr(_faker_local, "faker", None)
    if f is None:
        f = Faker("en_GB")
        _faker_local.faker = f
    return f


# --------------------------------------------------------------------------------------
# Record entities
# --------------------------------------------------------------------------------------

@dataclass
class Person:
    sex: str
    title: str
    first_name: str
    middle_name: str
    last_name: str
    dob: date
    age: int
    relationship: str
    address_line_1: str
    address_line_2: str
    town: str
    county: str
    postcode: str
    phone_mobile: str
    phone_home: str
    email: str
    nhs_number: str
    hospital_number: str
    marital_status: str
    ethnicity: str
    occupation: str
    signature: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def summary(self) -> dict:
        return {
            "name": self.full_name, "sex": self.sex, "age": self.age,
            "date_of_birth": self.dob.isoformat(), "town": self.town,
            "relationship_to_patient": self.relationship, "occupation": self.occupation,
            "marital_status": self.marital_status,
        }


@dataclass
class Gp:
    name: str
    practice: str
    address: str
    phone: str
    practice_code: str


FAMILY = [
    ("Wife", "Female", "peer"), ("Husband", "Male", "peer"), ("Partner", None, "peer"),
    ("Son", "Male", "child"), ("Daughter", "Female", "child"), ("Mother", "Female", "parent"),
    ("Father", "Male", "parent"), ("Brother", "Male", "peer"), ("Sister", "Female", "peer"),
]
NON_FAMILY = ["Friend", "Neighbour", "Carer"]


def nhs_number(rng: random.Random) -> str:
    """A valid (Modulus 11) NHS number in the 999 test range."""
    while True:
        digits = [9, 9, 9] + [rng.randint(0, 9) for _ in range(6)]
        total = sum(d * (10 - i) for i, d in enumerate(digits))
        check = 11 - (total % 11)
        if check == 11:
            check = 0
        if check == 10:
            continue
        s = "".join(map(str, digits)) + str(check)
        return f"{s[:3]} {s[3:6]} {s[6:]}"


def _postcode(rng, area, lo, hi) -> str:
    L = uk_data.POSTCODE_INWARD_LETTERS
    return f"{area}{rng.randint(lo, hi)} {rng.randint(0, 9)}{rng.choice(L)}{rng.choice(L)}"


def _landline(rng, code) -> str:
    if len(code) == 3:
        return f"{code} {rng.randint(3000, 8999)} {rng.randint(0, 9999):04d}"
    if len(code) == 4:
        return f"{code} {rng.randint(200, 899)} {rng.randint(0, 9999):04d}"
    return f"{code} {rng.randint(200000, 899999)}"


def _street(rng, fake) -> str:
    stem = rng.choice(uk_data.STREET_PREFIXES) if rng.random() < 0.65 else fake.last_name()
    names, weights = zip(*uk_data.STREET_SUFFIXES)
    return f"{stem} {rng.choices(names, weights)[0]}"


def _mobile(rng) -> str:
    return f"07{rng.randint(100, 999)} {rng.randint(100000, 999999)}"


def _title(rng, sex, age) -> str:
    if age < 16:
        return "Master" if sex == "Male" else "Miss"
    if rng.random() < 0.02:
        return "Dr"
    if sex == "Male":
        return "Mr"
    if age < 30:
        return rng.choices(["Miss", "Ms", "Mrs"], [5, 3, 2])[0]
    return rng.choices(["Mrs", "Ms", "Miss"], [6, 3, 1])[0]


def _signature_text(rng, first, last) -> str:
    return rng.choice([f"{first[0]} {last}", f"{first} {last}", f"{first[0]}. {last}", f"{first[0]}{last}", last])


# --------------------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------------------

@dataclass
class FieldSpec:
    name: str
    type_key: str
    person: int = 1


class GenerationError(Exception):
    pass


class SetContext:
    def __init__(self, seed: int, types: dict[str, "TypeDef"], field_specs: dict[str, FieldSpec],
                 age_min: int = 18, age_max: int = 95, today: date | None = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self._fake_rng = random.Random(seed ^ 0x5F3759DF)
        self.types = types
        self.field_specs = field_specs
        self.age_min, self.age_max = sorted((max(0, age_min), min(105, age_max)))
        self.today = today or date.today()
        self.persons: dict[int, Person] = {}
        self.cache: dict[Any, Any] = {}
        self.values: dict[str, Any] = {}
        self._resolving: set[str] = set()

    @property
    def fake(self) -> Faker:
        # One Faker per thread, but each context drives it with its own random stream so
        # interleaved contexts (e.g. a preview during a run) stay deterministic.
        f = _faker()
        f.random = self._fake_rng
        return f

    # ---- entities
    def person(self, idx: int) -> Person:
        idx = max(1, int(idx or 1))
        if idx not in self.persons:
            self.persons[idx] = self._make_person(idx)
        return self.persons[idx]

    def gp(self) -> Gp:
        if "gp" not in self.cache:
            rng, fake, p1 = self.rng, self.fake, self.person(1)
            town = next((t for t in uk_data.TOWNS if t[0] == p1.town), rng.choice(uk_data.TOWNS))
            street = _street(rng, fake)
            practice = rng.choice(uk_data.GP_PRACTICE_PATTERNS).format(
                town=town[0], locality=rng.choice(uk_data.LOCALITIES), street=street.split(" ")[0])
            self.cache["gp"] = Gp(
                name=f"Dr {fake.first_name()[0]} {fake.last_name()}",
                practice=practice,
                address=f"{rng.randint(1, 120)} {street}\n{town[0]}\n{_postcode(rng, town[2], town[3], town[4])}",
                phone=_landline(rng, town[5]),
                practice_code=f"{rng.choice('ABCDEFGHJKLMNPY')}{rng.randint(10000, 99999)}",
            )
        return self.cache["gp"]

    def _make_person(self, idx: int) -> Person:
        rng, fake, today = self.rng, self.fake, self.today
        p1 = self.person(1) if idx > 1 else None  # related people are derived from the patient
        relationship, share_surname, share_address = "Self", False, False
        if p1 is None:
            sex = rng.choice(["Male", "Female"])
            age = rng.randint(self.age_min, self.age_max)
        elif idx >= 3:
            # persons 3+ are other people on the form (clinicians, contacts): unrelated adults
            relationship = "Other"
            sex = rng.choice(["Male", "Female"])
            age = rng.randint(25, 65)
        else:
            if rng.random() < 0.65:
                rel, sex_req, rule = rng.choice(FAMILY)
                if rule == "child" and p1.age - 20 < 18:
                    rel, sex_req, rule = rng.choice(FAMILY[:3])
                if rule == "parent" and p1.age + 20 > 100:
                    rel, sex_req, rule = rng.choice(FAMILY[3:5])
                age = {"peer": lambda: p1.age + rng.randint(-8, 8),
                       "child": lambda: p1.age - rng.randint(20, min(40, p1.age - 18)),
                       "parent": lambda: p1.age + rng.randint(20, min(35, 100 - p1.age))}[rule]()
                age = max(18, min(100, age))
                sex = sex_req or rng.choice(["Male", "Female"])
                relationship = rel
                share_surname = rel not in ("Partner",) and (rel != "Sister" or rng.random() < 0.6)
                share_address = rel in ("Wife", "Husband", "Partner") or rng.random() < 0.3
            else:
                relationship = rng.choice(NON_FAMILY)
                sex = rng.choice(["Male", "Female"])
                age = rng.randint(25, 80)

        dob = date(today.year - age, 1, 1) + timedelta(days=rng.randint(0, 364))
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        first = fake.first_name_male() if sex == "Male" else fake.first_name_female()
        middle = (fake.first_name_male() if sex == "Male" else fake.first_name_female()) if rng.random() < 0.6 else ""
        last = p1.last_name if (p1 and share_surname) else fake.last_name()

        if p1 and share_address:
            addr = (p1.address_line_1, p1.address_line_2, p1.town, p1.county, p1.postcode)
            home = p1.phone_home
        else:
            town = rng.choice(uk_data.TOWNS)
            street = _street(rng, fake)
            num = rng.randint(1, 180)
            if rng.random() < 0.15:
                line1, line2 = f"Flat {rng.randint(1, 24)}, {rng.choice(uk_data.BUILDING_NAMES)}", f"{num} {street}"
            else:
                line1 = f"{num} {street}"
                line2 = rng.choice(uk_data.LOCALITIES) if rng.random() < 0.4 else ""
            addr = (line1, line2, town[0], town[1], _postcode(rng, town[2], town[3], town[4]))
            home = _landline(rng, town[5])

        email_local = rng.choice([
            f"{first}.{last}", f"{first}{last}{rng.randint(1, 99)}", f"{first[0]}{last}",
            f"{first}_{last}{rng.randint(50, 99)}", f"{last}.{first[0]}",
        ]).lower().replace(" ", "").replace("'", "")
        occupation = "Retired" if age >= 67 and rng.random() < 0.8 else (
            "Student" if age < 22 and rng.random() < 0.6 else fake.job())
        marital = rng.choice(uk_data.MARITAL_STATUSES) if age >= 18 else "Single"
        if relationship in ("Wife", "Husband"):
            marital = "Married"

        return Person(
            sex=sex, title=_title(rng, sex, age), first_name=first, middle_name=middle, last_name=last,
            dob=dob, age=age, relationship=relationship,
            address_line_1=addr[0], address_line_2=addr[1], town=addr[2], county=addr[3], postcode=addr[4],
            phone_mobile=_mobile(rng), phone_home=home, email=f"{email_local}@{rng.choice(uk_data.EMAIL_DOMAINS)}",
            nhs_number=nhs_number(rng),
            hospital_number=f"{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.randint(1000000, 9999999)}",
            marital_status=marital, ethnicity=rng.choice(uk_data.ETHNICITIES), occupation=occupation,
            signature=_signature_text(rng, first, last),
        )

    # ---- values
    def field_value(self, name: str) -> Any:
        if name in self.values:
            return self.values[name]
        spec = self.field_specs.get(name)
        if spec is None:
            raise GenerationError(f"Unknown field '{name}'")
        if name in self._resolving:
            raise GenerationError(f"Circular reference involving field '{name}'")
        self._resolving.add(name)
        try:
            v = self.type_value(spec.type_key, spec.person, name)
        finally:
            self._resolving.discard(name)
        self.values[name] = v
        return v

    def ref(self, name: str, person: int) -> Any:
        """Resolve a reference from a composite/relative type: a field name first, then a type key."""
        if name in self.field_specs:
            return self.field_value(name)
        if name in self.types:
            return self.type_value(name, person, None)
        raise GenerationError(f"Unknown reference '{{{name}}}'")

    def type_value(self, key: str, person: int = 1, field_name: str | None = None) -> Any:
        t = self.types.get(key)
        if t is None:
            raise GenerationError(f"Unknown data type '{key}'")
        return t.generate(self, person, field_name)


# --------------------------------------------------------------------------------------
# Built-in types
# --------------------------------------------------------------------------------------

def _med(ctx: SetContext, p: int, fname: str | None):
    group = re.search(r"\d+", fname or "")
    k = ("med", p, group.group(0) if group else "")
    if k not in ctx.cache:
        ctx.cache[k] = ctx.rng.choice(uk_data.MEDICATIONS)
    return ctx.cache[k]


def _gmc(ctx: SetContext, p: int) -> str:
    k = ("gmc", p)
    if k not in ctx.cache:
        ctx.cache[k] = str(ctx.rng.randint(2000000, 7999999))
    return ctx.cache[k]


def _work_email(pp: Person) -> str:
    clean = lambda s: re.sub(r"[^a-z]", "", s.lower())
    return f"{clean(pp.first_name)}.{clean(pp.last_name)}@nhs.net"


def _days(ctx, lo, hi):
    return ctx.today + timedelta(days=ctx.rng.randint(lo, hi))


def _addr_lines(pp: Person) -> list[str]:
    return [l for l in (pp.address_line_1, pp.address_line_2, pp.town, pp.postcode) if l]


Gen = Callable[[SetContext, int, "str | None"], Any]

# key, label, category, value_kind, generator, suggested render
_BUILTINS: list[tuple[str, str, str, str, Gen, str]] = [
    # Person
    ("title", "Title", "Person", "text", lambda c, p, f: c.person(p).title, "text"),
    ("first_name", "First name", "Person", "text", lambda c, p, f: c.person(p).first_name, "text"),
    ("middle_name", "Middle name", "Person", "text", lambda c, p, f: c.person(p).middle_name, "text"),
    ("last_name", "Last name / surname", "Person", "text", lambda c, p, f: c.person(p).last_name, "text"),
    ("full_name", "Full name", "Person", "text", lambda c, p, f: c.person(p).full_name, "text"),
    ("full_name_with_title", "Full name with title", "Person", "text",
     lambda c, p, f: f"{c.person(p).title} {c.person(p).full_name}", "text"),
    ("initials", "Initials", "Person", "text",
     lambda c, p, f: "".join(n[0] for n in (c.person(p).first_name, c.person(p).middle_name, c.person(p).last_name) if n), "text"),
    ("sex", "Sex (Male/Female)", "Person", "text", lambda c, p, f: c.person(p).sex, "text"),
    ("date_of_birth", "Date of birth", "Person", "date", lambda c, p, f: c.person(p).dob, "text"),
    ("age", "Age", "Person", "number", lambda c, p, f: c.person(p).age, "text"),
    ("nhs_number", "NHS number (999 test range)", "Person", "text", lambda c, p, f: c.person(p).nhs_number, "comb"),
    ("hospital_number", "Hospital number (MRN)", "Person", "text", lambda c, p, f: c.person(p).hospital_number, "text"),
    ("marital_status", "Marital status", "Person", "text", lambda c, p, f: c.person(p).marital_status, "text"),
    ("ethnicity", "Ethnicity (NHS categories)", "Person", "text", lambda c, p, f: c.person(p).ethnicity, "text"),
    ("occupation", "Occupation", "Person", "text", lambda c, p, f: c.person(p).occupation, "text"),
    ("relationship", "Relationship to patient", "Person", "text", lambda c, p, f: c.person(p).relationship, "text"),
    ("signature", "Signature", "Person", "text", lambda c, p, f: c.person(p).signature, "signature"),
    # Contact
    ("phone_mobile", "Mobile number", "Contact", "text", lambda c, p, f: c.person(p).phone_mobile, "text"),
    ("phone_home", "Home phone number", "Contact", "text", lambda c, p, f: c.person(p).phone_home, "text"),
    ("email", "Email address", "Contact", "text", lambda c, p, f: c.person(p).email, "text"),
    # Address
    ("address_line_1", "Address line 1", "Address", "text", lambda c, p, f: c.person(p).address_line_1, "text"),
    ("address_line_2", "Address line 2", "Address", "text", lambda c, p, f: c.person(p).address_line_2, "text"),
    ("town", "Town / city", "Address", "text", lambda c, p, f: c.person(p).town, "text"),
    ("county", "County", "Address", "text", lambda c, p, f: c.person(p).county, "text"),
    ("postcode", "Postcode", "Address", "text", lambda c, p, f: c.person(p).postcode, "text"),
    ("full_address", "Full address (multi-line)", "Address", "text",
     lambda c, p, f: "\n".join(_addr_lines(c.person(p))), "multiline"),
    ("address_single_line", "Full address (one line)", "Address", "text",
     lambda c, p, f: ", ".join(_addr_lines(c.person(p))), "text"),
    # GP
    ("gp_name", "GP name", "GP", "text", lambda c, p, f: c.gp().name, "text"),
    ("gp_practice", "GP practice name", "GP", "text", lambda c, p, f: c.gp().practice, "text"),
    ("gp_address", "GP practice address (multi-line)", "GP", "text", lambda c, p, f: c.gp().address, "multiline"),
    ("gp_phone", "GP practice phone", "GP", "text", lambda c, p, f: c.gp().phone, "text"),
    ("gp_practice_code", "GP practice (ODS) code", "GP", "text", lambda c, p, f: c.gp().practice_code, "text"),
    # Clinical
    ("clinician_name", "Clinician name (Dr …)", "Clinical", "text", lambda c, p, f: f"Dr {c.person(p).full_name}", "text"),
    ("gmc_number", "GMC / professional registration number", "Clinical", "text", lambda c, p, f: _gmc(c, p), "text"),
    ("work_email", "Work email (nhs.net)", "Contact", "text", lambda c, p, f: _work_email(c.person(p)), "text"),
    ("condition", "Condition / diagnosis", "Clinical", "text", lambda c, p, f: c.rng.choice(uk_data.CONDITIONS), "text"),
    ("medication_name", "Medication name", "Clinical", "text", lambda c, p, f: _med(c, p, f)[0], "text"),
    ("medication_dose", "Medication dose (pairs with medication name)", "Clinical", "text", lambda c, p, f: _med(c, p, f)[1], "text"),
    ("medication_with_dose", "Medication with dose", "Clinical", "text",
     lambda c, p, f: " ".join(c.rng.choice(uk_data.MEDICATIONS)), "text"),
    ("allergies", "Allergies", "Clinical", "text", lambda c, p, f: c.rng.choice(uk_data.ALLERGIES), "text"),
    ("height_cm", "Height (cm)", "Clinical", "number", lambda c, p, f: c.rng.randint(150, 195), "text"),
    ("weight_kg", "Weight (kg)", "Clinical", "number", lambda c, p, f: round(c.rng.uniform(45, 120), 1), "text"),
    # Dates & times
    ("today", "Today's date", "Dates", "date", lambda c, p, f: c.today, "text"),
    ("recent_date", "Recent date (last 90 days)", "Dates", "date", lambda c, p, f: _days(c, -90, -1), "text"),
    ("future_date", "Future date (next 90 days)", "Dates", "date", lambda c, p, f: _days(c, 1, 90), "text"),
    ("past_date", "Past date (last 5 years)", "Dates", "date", lambda c, p, f: _days(c, -1825, -1), "text"),
    ("time", "Time (HH:MM)", "Dates", "text",
     lambda c, p, f: f"{c.rng.randint(8, 18):02d}:{c.rng.choice(range(0, 60, 5)):02d}", "text"),
    # Other
    ("yes_no", "Yes / No (50%)", "Other", "bool", lambda c, p, f: c.rng.random() < 0.5, "checkbox"),
    ("lorem_text", "Placeholder text (lorem ipsum)", "Other", "text", lambda c, p, f: c.fake.sentence(nb_words=8), "multiline"),
    ("company", "Company name", "Other", "text", lambda c, p, f: c.fake.company(), "text"),
]


# --------------------------------------------------------------------------------------
# Custom kinds
# --------------------------------------------------------------------------------------

def gen_pattern(rng: random.Random, pattern: str) -> str:
    """# digit, ? letter (A-Z), * letter or digit, [ABC] one of, \\x literal x."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i + 1]); i += 2; continue
        if ch == "#":
            out.append(str(rng.randint(0, 9)))
        elif ch == "?":
            out.append(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        elif ch == "*":
            out.append(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"))
        elif ch == "[":
            j = pattern.find("]", i)
            if j > i + 1:
                out.append(rng.choice(pattern[i + 1:j])); i = j + 1; continue
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)(?::([^}]*))?\}")


def _to_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                pass
    return None


def _gen_custom(t: "TypeDef", ctx: SetContext, person: int, fname: str | None):
    c, rng = t.config, ctx.rng
    k = t.kind
    if k == "picklist":
        values = [v for v in c.get("values", []) if str(v).strip()]
        if not values:
            return ""
        weights = c.get("weights")
        if weights and len(weights) == len(values):
            return rng.choices(values, weights=[max(0.0, float(w)) for w in weights])[0]
        return rng.choice(values)
    if k == "pattern":
        return gen_pattern(rng, c.get("pattern", ""))
    if k == "number_range":
        lo, hi = float(c.get("min", 0)), float(c.get("max", 100))
        dec = int(c.get("decimals", 0))
        n = rng.randint(int(lo), int(hi)) if dec == 0 else round(rng.uniform(lo, hi), dec)
        txt = f"{n:.{dec}f}" if dec else str(n)
        return f"{c.get('prefix', '')}{txt}{c.get('suffix', '')}"
    if k == "date_range":
        mode = c.get("mode", "relative_today")
        lo, hi = int(c.get("min_days", -30)), int(c.get("max_days", 0))
        if hi < lo:
            lo, hi = hi, lo
        if mode == "absolute":
            s, e = _to_date(c.get("start")) or ctx.today, _to_date(c.get("end")) or ctx.today
            if e < s:
                s, e = e, s
            return s + timedelta(days=rng.randint(0, (e - s).days))
        if mode == "relative_to":
            base = _to_date(ctx.ref(c.get("base", "today"), person)) or ctx.today
            return base + timedelta(days=rng.randint(lo, hi))
        return ctx.today + timedelta(days=rng.randint(lo, hi))
    if k == "composite":
        def sub(m):
            v = ctx.ref(m.group(1), person)
            fmt = m.group(2)
            if isinstance(v, (date, datetime)):
                return v.strftime(fmt or "%d/%m/%Y")
            if v is AI_PENDING or v is None:
                return ""
            return str(v)
        return re.sub(r"\s{2,}", " ", _PLACEHOLDER.sub(sub, c.get("template", ""))).strip()
    if k == "boolean":
        return rng.random() * 100 < float(c.get("true_pct", 50))
    if k == "ai":
        if c.get("mode") == "pool":
            pool = [v for v in c.get("pool", []) if str(v).strip()]
            return rng.choice(pool) if pool else ""
        return AI_PENDING
    raise GenerationError(f"Unknown kind '{k}'")


# --------------------------------------------------------------------------------------
# Type registry
# --------------------------------------------------------------------------------------

@dataclass
class TypeDef:
    key: str
    label: str
    kind: str
    category: str
    value_kind: str
    description: str = ""
    config: dict = field(default_factory=dict)
    builtin: bool = False
    render: str = "text"
    fn: Gen | None = None
    per_person: bool = False  # the value depends on which person the field is for

    def generate(self, ctx: SetContext, person: int, fname: str | None):
        if self.fn is not None:
            return self.fn(ctx, person, fname)
        return _gen_custom(self, ctx, person, fname)

    def public(self) -> dict:
        return {
            "key": self.key, "label": self.label, "kind": self.kind, "category": self.category,
            "value_kind": self.value_kind, "description": self.description, "config": self.config,
            "builtin": self.builtin, "render": self.render, "per_person": self.per_person,
        }


BUILTIN_TYPES: dict[str, TypeDef] = {
    k: TypeDef(key=k, label=label, kind="builtin", category=cat, value_kind=vk, builtin=True, render=render, fn=fn,
               per_person=cat in ("Person", "Contact", "Address") or k in ("clinician_name", "gmc_number"))
    for k, label, cat, vk, fn, render in _BUILTINS
}

CUSTOM_VALUE_KIND = {"number_range": "text", "date_range": "date", "boolean": "bool"}
CUSTOM_RENDER = {"boolean": "checkbox"}


def custom_typedef(row: dict) -> TypeDef:
    kind = row["kind"]
    return TypeDef(
        key=row["key"], label=row["label"], kind=kind, category=row.get("category") or "Custom",
        value_kind=CUSTOM_VALUE_KIND.get(kind, "text"), description=row.get("description", ""),
        config=row.get("config") or {}, render=CUSTOM_RENDER.get(kind, "multiline" if kind == "ai" else "text"),
    )


def all_types() -> dict[str, TypeDef]:
    types = dict(BUILTIN_TYPES)
    for row in db.query("SELECT * FROM data_types ORDER BY label"):
        types[row["key"]] = custom_typedef(row)
    return types


KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_custom(key: str, kind: str, config: dict) -> None:
    if not KEY_RE.match(key):
        raise ValueError("Key must start with a letter and use only lowercase letters, digits and underscores")
    if key in BUILTIN_TYPES:
        raise ValueError(f"'{key}' is a built-in type")
    if kind not in CUSTOM_KINDS:
        raise ValueError(f"Unknown kind '{kind}'")
    if kind == "picklist" and not [v for v in config.get("values", []) if str(v).strip()]:
        raise ValueError("A pick-list needs at least one value")
    if kind == "pattern" and not config.get("pattern"):
        raise ValueError("A pattern is required")
    if kind == "composite" and not config.get("template"):
        raise ValueError("A template is required, e.g. {title} {first_name} {last_name}")
    if kind == "ai":
        if not config.get("prompt"):
            raise ValueError("An AI type needs a prompt")
        if config.get("mode") == "pool" and not [v for v in config.get("pool", []) if str(v).strip()]:
            raise ValueError("Pool mode needs a generated pool of values")


# --------------------------------------------------------------------------------------
# Formatting (per field)
# --------------------------------------------------------------------------------------

DATE_FORMATS = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d%m%Y"]


def format_value(value: Any, opts: dict) -> str:
    if value is None or value is AI_PENDING:
        return ""
    if isinstance(value, bool):
        s = (opts.get("true_text") or "Yes") if value else (opts.get("false_text") or "No")
    elif isinstance(value, (date, datetime)):
        try:
            s = value.strftime(opts.get("date_format") or "%d/%m/%Y")
        except ValueError:
            s = value.strftime("%d/%m/%Y")
    else:
        s = str(value)
    t = opts.get("transform") or ""
    if t == "upper":
        s = s.upper()
    elif t == "lower":
        s = s.lower()
    elif t == "title":
        s = s.title()
    elif t == "no_spaces":
        s = s.replace(" ", "")
    elif t == "single_line":
        s = ", ".join(x.strip() for x in s.splitlines() if x.strip())
    return f"{opts.get('prefix', '')}{s}{opts.get('suffix', '')}"


def raw_json(value: Any):
    if value is AI_PENDING:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def sample_values(t: TypeDef, n: int = 5, seed: int | None = None) -> list[str]:
    """Preview values for a type (live AI types return a placeholder)."""
    types = all_types()
    types[t.key] = t
    out = []
    base = seed if seed is not None else random.randint(0, 10**9)
    for i in range(n):
        ctx = SetContext(base + i, types, {})
        v = ctx.type_value(t.key, 1, None)
        out.append("(generated by AI during runs)" if v is AI_PENDING else format_value(v, {}))
    return out
