from libcoverdls.schema import SchemaRDLS
from libcoverdls.config import LibCoveRDLSConfig
from libcoverdls.jsonschemavalidate import JSONSchemaValidator
from libcoverdls.additionalfields import AdditionalFields
import libcoverdls.run_tasks
import libcoverdls.data_reader

from cove_rdls.lib.utils import group_validation_errors

from typing import List
import json
import os
import magic

import flattentool
from sentry_sdk import capture_exception

from libcoveweb2.models import SuppliedDataFile, SuppliedData
from libcoveweb2.process.base import ProcessDataTask
from libcoveweb2.process.common_tasks.task_with_state import TaskWithState

# from libcove.lib.converters import convert_json, convert_spreadsheet
from libcoveweb2.utils import get_file_type_for_flatten_tool
from libcoveweb2.utils import group_data_list_by


class Sample(ProcessDataTask):
    def is_processing_applicable(self) -> bool:
        return True

    def process(self, process_data: dict) -> dict:
        process_data["sample_mode"] = self.supplied_data.meta.get("sample_mode")
        return process_data

    def get_context(self):
        return {"sample_mode": self.supplied_data.meta.get("sample_mode")}


class SetOrTestSuppliedDataFormat(ProcessDataTask):

    map_file_type_to_format = {
        'json': 'json',
        'xlsx': 'spreadsheet',
        'ods': 'spreadsheet'
    }

    def is_processing_applicable(self) -> bool:
        return True

    def is_processing_needed(self) -> bool:
        return self.supplied_data.format == "unknown"

    def _add_extention(self, supplied_data_file):
        input_filename = supplied_data_file.upload_dir_and_filename()
        filename = input_filename.split("/")[-1]
        if supplied_data_file.source_method == "url":
            if "." not in filename:
                content_type = magic.from_file(input_filename, mime=True)
                file_renamed = False
                if content_type == 'application/json':
                    file_renamed = f"{input_filename}.json"
                elif content_type == 'text/csv':
                    file_renamed = f"{input_filename}.csv"
                elif (content_type == 'application/octet-stream' or content_type ==
                      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'):
                    file_renamed = f"{input_filename}.xlsx"
                if file_renamed:
                    os.symlink(input_filename, file_renamed)
                    supplied_data_file.filename = file_renamed.split("/")[-1]
                    supplied_data_file.save()
#        raise Exception(f"add_extention - source_method: {supplied_data_file.source_method}, ",
#                        f"filename: {filename}, content_type: {content_type}, file_renamed: {file_renamed}")

    def process(self, process_data: dict) -> dict:
        if self.supplied_data.format == "unknown":
            # Look up what data format is, and set it
            supplied_data_files = SuppliedDataFile.objects.filter(
                supplied_data=self.supplied_data
            )
            for supplied_data_file in supplied_data_files:
                self._add_extention(supplied_data_file)
            all_file_types = [get_file_type_for_flatten_tool(i) for i in supplied_data_files]
            file_types_reduced = list(set([i for i in all_file_types if i]))
            if len(file_types_reduced) == 1:
                self.supplied_data.format = self.map_file_type_to_format[file_types_reduced[0]]
                self.supplied_data.save()

            elif len(file_types_reduced) == 0:
                raise NotImplementedError("GOT ZERO")
                # TODO

            elif len(file_types_reduced) > 1:
                raise NotImplementedError("GOT MORE THAN ONE")
                # TODO

        return process_data

    def get_context(self):
        return {"original_format": self.supplied_data.format}


class WasJSONUploaded(ProcessDataTask):
    """Did user upload JSON?
    Then we don't actually have to do anything, but we want to save info about that JSON for later steps."""

    def is_processing_applicable(self) -> bool:
        return True

    def process(self, process_data: dict) -> dict:
        if self.supplied_data.format != "json":
            return process_data

        supplied_data_json_files = [i for i in self.supplied_data_files if
                                    get_file_type_for_flatten_tool(i) == "json"]
        if len(supplied_data_json_files) == 1:
            process_data[
                "json_data_filename"
            ] = supplied_data_json_files[0].upload_dir_and_filename()
        else:
            raise Exception("Can't find JSON original data!")

        return process_data

    def get_context(self):
        return {}


CONVERT_SPREADSHEET_INTO_JSON_DIR_NAME = "unflatten"


class ConvertSpreadsheetIntoJSON(ProcessDataTask):
    """If User uploaded Spreadsheet, convert to our primary format, JSON."""

    def __init__(
        self, supplied_data: SuppliedData, supplied_data_files: List[SuppliedDataFile]
    ):
        super().__init__(supplied_data, supplied_data_files)
        self.data_filename = os.path.join(
            self.supplied_data.data_dir(),
            CONVERT_SPREADSHEET_INTO_JSON_DIR_NAME,
            "unflattened.json",
        )

    def is_processing_applicable(self) -> bool:
        return self.supplied_data.format == "spreadsheet"

    def is_processing_needed(self) -> bool:
        return self.supplied_data.format == "spreadsheet" and not os.path.exists(
            self.data_filename
        )

    def _clean_and_fix_json(self):
        output_dir = os.path.join(
            self.supplied_data.data_dir(), CONVERT_SPREADSHEET_INTO_JSON_DIR_NAME
        )
        with open(os.path.join(output_dir, "unflattened.json"), "r+") as f:
            json_data = json.loads(f.read())
            json_data = {"datasets": [dataset for dataset in json_data["datasets"] if len(dataset) > 1]}
            f.seek(0)
            f.write(json.dumps(json_data))
            f.truncate()

#    def _fix_filename(self, supplied_data_json_file):
#        input_filename = supplied_data_json_file.upload_dir_and_filename()
#        filename = input_filename.split("/")[-1]
#        if self.supplied_data.source_method == "url":
#            if "." not in filename:
#                file_renamed = f"{input_filename}.xlsx"
#                os.symlink(input_filename, file_renamed)
#                return file_renamed
#        return input_filename

    def process(self, process_data: dict) -> dict:
        if self.supplied_data.format != "spreadsheet":
            return process_data

        process_data["json_data_filename"] = self.data_filename

        # check already done
        if os.path.exists(self.data_filename):
            return process_data

        supplied_data_json_files = SuppliedDataFile.objects.filter(
            supplied_data=self.supplied_data
        )
        if supplied_data_json_files.count() != 1:
            raise Exception("Can't find Spreadsheet original data!")

        supplied_data_json_file = supplied_data_json_files.first()
#        input_filename = self._fix_filename(supplied_data_json_file)
        input_filename = supplied_data_json_file.upload_dir_and_filename()

        output_dir = os.path.join(
            self.supplied_data.data_dir(), CONVERT_SPREADSHEET_INTO_JSON_DIR_NAME
        )

        os.makedirs(output_dir, exist_ok=True)

        # We don't know what schema version the spreadsheet is in. Use default schema.
        schema = SchemaRDLS()

        unflatten_kwargs = {
            "output_name": os.path.join(output_dir, "unflattened.json"),
            "cell_source_map": os.path.join(output_dir, "cell_source_map.json"),
            "heading_source_map": os.path.join(output_dir, "heading_source_map.json"),
            "metatab_name": "Meta",
            "id_name": "id",
            "root_list_path": "datasets",
            "root_id": "id",
            "convert_wkt": True,
            "input_format": get_file_type_for_flatten_tool(supplied_data_json_file),
            "schema": schema.pkg_schema_url,
        }

        flattentool.unflatten(input_filename, **unflatten_kwargs)

#        raise Exception(f"input_filename: {input_filename}, unflatten_kwargs: {unflatten_kwargs}")

#        self._clean_and_fix_json()

        return process_data

    def get_context(self):
        context = {}
        # original format
        if self.supplied_data.format == "spreadsheet":
            context["original_format"] = "spreadsheet"
            # Download data
            if os.path.exists(self.data_filename):
                context["can_download_json"] = True
                context["download_json_url"] = os.path.join(
                    self.supplied_data.data_url(),
                    CONVERT_SPREADSHEET_INTO_JSON_DIR_NAME,
                    "unflattened.json",
                )
                context["download_json_size"] = os.stat(self.data_filename).st_size
            else:
                context["can_download_json"] = False
        # Return
        return context


class GetDataReaderAndConfigAndSchema(ProcessDataTask):
    def __init__(
        self, supplied_data: SuppliedData, supplied_data_files: List[SuppliedDataFile]
    ):
        super().__init__(supplied_data, supplied_data_files)
        self.data_filename = os.path.join(
            self.supplied_data.data_dir(), "schema.json"
        )

    def is_processing_applicable(self) -> bool:
        return True

    def is_processing_needed(self) -> bool:
        return False

    def process(self, process_data: dict) -> dict:
        # Make things and set info for later in processing
        process_data['data_reader'] = libcoverdls.data_reader.DataReader(
            process_data["json_data_filename"], sample_mode=process_data['sample_mode']
        )
        process_data['config'] = LibCoveRDLSConfig()
        process_data['schema'] = SchemaRDLS(process_data['data_reader'], process_data['config'])
        # Save some to disk for templates
        if not os.path.exists(self.data_filename):
            save_data = {
                "schema_version_used": process_data['schema'].schema_version
            }
            with open(self.data_filename, "w") as fp:
                json.dump(save_data, fp, indent=4)
        # return
        return process_data

    def get_context(self):
        context = {}
        # data
        if os.path.exists(self.data_filename):
            with open(self.data_filename) as fp:
                context.update(json.load(fp))
        # done!
        return context


CONVERT_JSON_INTO_SPREADSHEETS_DIR_NAME = "flatten"


class ConvertJSONIntoSpreadsheets(ProcessDataTask):
    """Convert primary format (JSON) to spreadsheets"""

    def __init__(
        self, supplied_data: SuppliedData, supplied_data_files: List[SuppliedDataFile]
    ):
        super().__init__(supplied_data, supplied_data_files)
        self.output_dir = os.path.join(
            self.supplied_data.data_dir(),
            CONVERT_JSON_INTO_SPREADSHEETS_DIR_NAME,
            "data",
        )
        self.xlsx_filename = os.path.join(
            self.supplied_data.data_dir(),
            CONVERT_JSON_INTO_SPREADSHEETS_DIR_NAME,
            "data.xlsx",
        )

    def is_processing_applicable(self) -> bool:
        return True

    def is_processing_needed(self) -> bool:
        return not os.path.exists(self.xlsx_filename)

    def process(self, process_data: dict) -> dict:

        # don't run if already done
        if os.path.exists(self.xlsx_filename):
            return process_data

        os.makedirs(self.output_dir, exist_ok=True)

        flatten_kwargs = {
            "output_name": self.output_dir,
            "root_list_path": "there-is-no-root-list-path",
            "root_id": "statementID",
            "id_name": "statementID",
            "root_is_list": True,
            "convert_wkt": True,
            "schema": process_data['schema'].schema_url,
        }

        try:
            flattentool.flatten(process_data["json_data_filename"], **flatten_kwargs)
        except Exception as err:
            capture_exception(err)
            # TODO log and show to user. https://github.com/Open-Telecoms-Data/cove-ofds/issues/24

        return process_data

    def get_context(self):
        context = {}
        # XLSX
        if os.path.exists(self.xlsx_filename):
            context["can_download_xlsx"] = True
            context["download_xlsx_url"] = os.path.join(
                self.supplied_data.data_url(),
                CONVERT_JSON_INTO_SPREADSHEETS_DIR_NAME,
                "data.xlsx",
            )
            context["download_xlsx_size"] = os.stat(self.xlsx_filename).st_size
        else:
            context["can_download_xlsx"] = False
        # done!
        return context


class PythonValidateTask(TaskWithState):

    state_filename: str = "python_validate.json"

    def process_get_state(self, process_data: dict) -> dict:
        context = libcoverdls.run_tasks.process_additional_checks(
            process_data['data_reader'],
            process_data['config'],
            process_data['schema'],
            task_classes=libcoverdls.run_tasks.TASK_CLASSES_IN_SAMPLE_MODE if
            process_data["sample_mode"] else libcoverdls.run_tasks.TASK_CLASSES
        )

        # counts
        context["additional_checks_count"] = len(context["additional_checks"])

        # Old code removed here
        unknown_schema_version_used = \
            [i for i in context['additional_checks'] if i['type'] == 'unknown_schema_version_used']
        context['unknown_schema_version_used'] = unknown_schema_version_used[0] \
            if unknown_schema_version_used else None
        context['inconsistent_schema_version_used_count'] = \
            len([i for i in context['additional_checks'] if i['type'] == 'inconsistent_schema_version_used'])

        context['checks_not_run_in_sample_mode'] = []
        if process_data["sample_mode"]:
            classes_not_run_in_sample_mode = [
                x for x in libcoverdls.run_tasks.TASK_CLASSES
                if x not in libcoverdls.run_tasks.TASK_CLASSES_IN_SAMPLE_MODE
            ]
            for class_not_run_in_sample_mode in classes_not_run_in_sample_mode:
                context['checks_not_run_in_sample_mode'].extend(
                    class_not_run_in_sample_mode.get_additional_check_types_possible(
                        process_data['config'],
                        process_data['schema']
                    )
                )
            context['checks_not_run_in_sample_mode'] = list(set(context['checks_not_run_in_sample_mode']))

        return context, process_data


class JsonSchemaValidateTask(TaskWithState):

    state_filename: str = "jsonschema_validate.json"

    def process_get_state(self, process_data: dict) -> dict:
        worker = JSONSchemaValidator(process_data['schema'])

        # Get list of validation errors
        validation_errors = worker.validate(process_data['data_reader'])
        validation_errors = [i.json() for i in validation_errors]

        # Context
        context = {
            "validation_errors_count": len(validation_errors),
            "validation_errors": group_data_list_by(
                validation_errors, lambda i: f"{i['validator']}_{i['validator_value']}_{str(i['path_ending'])}"
            )
        }
        context["validation_errors_grouped"] = group_validation_errors(context["validation_errors"])

        return context, process_data


class AdditionalFieldsChecksTask(TaskWithState):

    state_filename: str = "additional_fields.json"

    def process_get_state(self, process_data: dict) -> dict:
        worker = AdditionalFields(process_data['schema'])

        output = worker.process(process_data['data_reader'])
        context = {"additional_fields": output}
        context["any_additional_fields_exist"] = len(output) > 0

        return context, process_data
