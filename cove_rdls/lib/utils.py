import json
from collections import defaultdict


def group_validation_errors(validation_errors):
    validation_errors_grouped = defaultdict(list)
    for key in validation_errors:
        vtype = validation_errors[key][0]["validator"]
        if vtype == "required":
            validation_errors_grouped["required"].append(validation_errors[key])
        elif error["validator"] in [
            "format",
            "pattern",
            "number",
            "string",
            "date-time",
            "uri",
            "object",
            "integer",
            "array",
        ]:
            validation_errors_grouped["format"].append(validation_errors[key])
        else:
            validation_errors_grouped["other"].append(validation_errors[key])
    return validation_errors_grouped
