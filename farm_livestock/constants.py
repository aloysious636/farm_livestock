"""Shared vocabularies (dropdown values, stage lists, metric definitions)."""
from __future__ import annotations

SPECIES = ("Cattle", "Swine", "Sheep", "Goat", "Rabbit")
SEXES = ("Male", "Female", "Castrate")
STATUSES = ("Active", "Sold", "Culled", "Deceased")

STAGES = {
    "Cattle": ("Calf", "Weaner", "Heifer", "Cow", "Lactating", "Dry", "Bull", "Steer"),
    "Swine": ("Piglet", "Weaner", "Grower", "Finisher", "Gilt", "Sow", "Lactating Sow", "Boar"),
    "Sheep": ("Lamb", "Weaner", "Ewe", "Lactating Ewe", "Ram", "Wether"),
    "Goat": ("Kid", "Weaner", "Doe", "Lactating Doe", "Buck", "Wether"),
    "Rabbit": ("Kit", "Weaner", "Doe", "Lactating Doe", "Buck"),
}
JUVENILE_STAGES = frozenset({"Calf", "Piglet", "Weaner", "Lamb", "Kid", "Kit"})
OFFSPRING_STAGE = {"Cattle": "Calf", "Swine": "Piglet", "Sheep": "Lamb", "Goat": "Kid", "Rabbit": "Kit"}
LACTATING_STAGE = {
    "Cattle": "Lactating", "Swine": "Lactating Sow", "Sheep": "Lactating Ewe",
    "Goat": "Lactating Doe", "Rabbit": "Lactating Doe",
}
WEANER_STAGE = "Weaner"

BREED_HINTS = {
    "Cattle": "Friesian, Ayrshire, Jersey, Guernsey, Boran, Sahiwal, Zebu cross",
    "Swine": "Large White, Landrace, Duroc, Hampshire, Camborough cross",
    "Sheep": "Dorper, Red Maasai, Blackhead Persian, Merino cross",
    "Goat": "Galla, Small East African, Saanen, Toggenburg, Alpine, Boer, Anglo-Nubian",
    "Rabbit": "New Zealand White, California White, Flemish Giant, Chinchilla",
}

BREEDING_STATUSES = ("Served", "Pregnant", "Open", "Aborted", "Delivered")
ACTIVE_BREEDING = ("Served", "Pregnant")
CLOSED_BREEDING = ("Open", "Aborted", "Delivered")
SERVICE_METHODS = ("Natural", "AI")

TASK_STATUSES = ("Pending", "Done", "Cancelled")  # "Overdue" is derived at read time
TASK_CATEGORIES = ("Health", "Husbandry", "Nutrition", "Reproduction")

METRICS = {
    "milk_yield_l": ("Daily milk yield", "litres"),
    "body_weight_kg": ("Body weight", "kg"),
    "birth_weight_kg": ("Birth weight", "kg"),
    "litter_size": ("Litter size born", "head"),
}

COST_CATEGORIES = ("Feed", "Medical", "Labour", "Other")
CURRENCY = "KES"
