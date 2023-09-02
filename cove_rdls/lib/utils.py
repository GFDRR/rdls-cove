from collections import defaultdict


def group_validation_errors(validation_errors):
    validation_errors_grouped = defaultdict(dict)
    for key in validation_errors:
        vtype = validation_errors[key][0]["validator"]
        if vtype == "required":
            validation_errors_grouped["required"][key] = validation_errors[key]
        elif vtype in [
            "format",
            "pattern",
            "number",
            "string",
            "date-time",
            "uri",
            "object",
            "integer",
            "array",
            "type",
        ]:
            validation_errors_grouped["format"][key] = validation_errors[key]
        else:
            validation_errors_grouped["other"][key] = validation_errors[key]
    return validation_errors_grouped
