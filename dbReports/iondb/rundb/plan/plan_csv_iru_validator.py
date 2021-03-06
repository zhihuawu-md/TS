# Copyright (C) 2016 Ion Torrent Systems, Inc. All Rights Reserved

from iondb.rundb.plan.views_helper import get_ir_set_id

from iondb.rundb.plan.views_helper import get_default_or_first_IR_account_by_userName
from iondb.rundb.sample.sample_validator import (
    validate_population,
    validate_mouseStrains,
    validate_sampleCollectionDate,
    validate_sampleReceiptDate,
)
from iondb.rundb.plan.plan_validator import validate_16s_markers
from plan_csv_writer import PlanCSVcolumns, get_irSettings
from iondb.rundb.models import Plugin
from traceback import format_exc
import requests
import json
import uuid
from datetime import datetime
import logging

from iondb.rundb.json_lazy import LazyJSONEncoder
from iondb.utils import i18n_errors, validation

logger = logging.getLogger(__name__)

"""
 This is main helper function which calls IRU api and process the response
    - Validate the iru configuration settings in the CSV plan upload.
    - construct the UserInputInfo for plan->selectedPlugins
"""


def get_userInfoDict(row, workflowObj, rowCount, setid_suffix, other_row_setIds):
    csv_setid = row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_SET_ID).strip()
    newSetID = True
    userInput_setid = None
    if other_row_setIds:
        for setID in other_row_setIds:
            check_setId = csv_setid + "__"
            if (
                check_setId in setID
                and workflowObj.get("RelationshipType") == "DNA_RNA"
            ):
                userInput_setid = setID
                newSetID = False
                break

    if not other_row_setIds or newSetID:
        userInput_setid = csv_setid + "__" + setid_suffix

    sampleName = row.get(PlanCSVcolumns.COLUMN_SAMPLE) or row.get(PlanCSVcolumns.COLUMN_SAMPLE_NAME, "")
    nucleotideType = row.get(PlanCSVcolumns.COLUMN_NUCLEOTIDE_TYPE) or ""
    sampleCollectionDate = row.get(PlanCSVcolumns.COLUMN_SAMPLE_COLLECTION_DATE) or ""
    if sampleCollectionDate:
        sampleCollectionDate = datetime.strptime(
            sampleCollectionDate.strip(), "%Y-%m-%d"
        ).date()
    sampleReceiptDate = row.get(PlanCSVcolumns.COLUMN_SAMPLE_RECEIPT_DATE) or ""
    if sampleReceiptDate:
        sampleReceiptDate = datetime.strptime(
            sampleReceiptDate.strip(), "%Y-%m-%d"
        ).date()

    userInputInfoDict = {
        "ApplicationType": workflowObj.get("ApplicationType"),
        "sample": sampleName.strip(),
        "Relation": workflowObj.get("RelationshipType"),
        "RelationRole": row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_RELATION),
        "Gender": row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_GENDER),
        "SampleCollectionDate": str(sampleCollectionDate),
        "SampleReceiptDate": str(sampleReceiptDate),
        "Population": row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_POPULATION),
        "mouseStrains": row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_MOUSE_STRAINS),
        "setid": userInput_setid,
        "cancerType": row.get(PlanCSVcolumns.COLUMN_SAMPLE_CANCER_TYPE),
        "cellularityPct": row.get(PlanCSVcolumns.COLUMN_SAMPLE_CELLULARITY),
        "biopsyDays": row.get(PlanCSVcolumns.COLUMN_SAMPLE_BIOPSY_DAYS),
        "barcodeId": row.get(PlanCSVcolumns.COLUMN_BARCODE),
        "cellNum": row.get(PlanCSVcolumns.COLUMN_SAMPLE_CELL_NUM),
        "coupleID": row.get(PlanCSVcolumns.COLUMN_SAMPLE_COUPLE_ID),
        "embryoID": row.get(PlanCSVcolumns.COLUMN_SAMPLE_EMBRYO_ID),
        "BacterialMarkerType": row.get(PlanCSVcolumns.COLUMN_SAMPLE_BACTERIAL_MARKER_TYPE, ""),
        "Witness": row.get(PlanCSVcolumns.COLUMN_SAMPLE_WITNESS, ""),
        "Workflow": row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_WORKFLOW),
        "nucleotideType": "RNA"
        if nucleotideType.upper() == "FUSIONS"
        else nucleotideType,
        "controlType": row.get(PlanCSVcolumns.COLUMN_SAMPLE_CONTROLTYPE),
        "tag_isFactoryProvidedWorkflow": workflowObj.get(
            "tag_isFactoryProvidedWorkflow"
        ),
        "row": str(rowCount),
    }

    return userInputInfoDict


def getWorkflowObj(workflow, USERINPUT):
    validWorkflowObj = None
    allWorkflowsObj = USERINPUT["workflows"]

    for obj in allWorkflowsObj:
        if workflow in obj["Workflow"]:
            validWorkflowObj = obj
            break

    return validWorkflowObj


def irWorkflowNotValid(workflow, USERINPUT):
    notValid = False
    workflowObj = getWorkflowObj(workflow, USERINPUT)

    if not workflowObj:
        notValid = True
    return notValid, workflowObj


def check_selected_values(planObj, samples_contents, csvPlanDict):
    userInput = []
    errorMsg = []
    errorMsgDict = {}
    USERINPUT = planObj.get_USERINPUT()
    errorDict = {
        "E001": "No samples available. Please check your input",
        "E002": "Selected Workflow is not compatible or invalid: %s",
        "E003": "Selected Cellularity % is not valid, should be 1 to 100: {0}",
        "E004": "Selected Gender is not valid, Valid values are {0}",
        "E005": "Selected Cancer Type is not valid, Valid values are {0}",
    }

    # process sample_contents for non barcoded samples
    isSingleCSV = False
    if csvPlanDict.get(PlanCSVcolumns.COLUMN_SAMPLE):
        isSingleCSV = True
        sampleName = csvPlanDict.get(PlanCSVcolumns.COLUMN_SAMPLE)
        samples_contents = [csvPlanDict]
    else:
        # Validate the main plan csv IR chevron workflow
        ir_chevron_workflow = csvPlanDict.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_WORKFLOW)
        notValid, workflowObj = irWorkflowNotValid(ir_chevron_workflow, USERINPUT)
        if notValid:
            msg = errorDict["E002"] % (ir_chevron_workflow)
            errorMsgDict[PlanCSVcolumns.COLUMN_SAMPLE_IR_WORKFLOW] = msg

    if not samples_contents:
        msg = errorDict["E001"]
        errorMsgDict[PlanCSVcolumns.COLUMN_SAMPLE] = msg
    else:
        for index, row in enumerate(samples_contents):
            setid_suffix = str(uuid.uuid4())
            errors = []
            rowCount = index + 1 if isSingleCSV else index + 3

            if not isSingleCSV:
                sampleName = row.get(PlanCSVcolumns.COLUMN_SAMPLE_NAME)
            if not sampleName:
                continue
            workflow = row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_WORKFLOW)
            notValid, workflowObj = irWorkflowNotValid(workflow, USERINPUT)

            # perform basic validation from TS side, rest of the validation will be executed from IRU
            # this would avoid the heavy lifting validation of IRU API call.
            if notValid:
                if isSingleCSV:
                    msg = errorDict["E002"] % (
                        row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_WORKFLOW)
                    )
                    errorMsgDict[PlanCSVcolumns.COLUMN_SAMPLE_IR_WORKFLOW] = msg
                else:
                    msg = errorDict["E002"] % (
                        row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_WORKFLOW)
                    )
                    errors.append(msg)

            # validate cellularity %
            cellularityPct = row.get(PlanCSVcolumns.COLUMN_SAMPLE_CELLULARITY)
            if cellularityPct:
                if not cellularityPct.isdigit():
                    errors.append(errorDict["E003"].format(cellularityPct))
                elif int(cellularityPct) not in range(1, 101):
                    errors.append(errorDict["E003"].format(cellularityPct))

            sampleCollectionDate = row.get(PlanCSVcolumns.COLUMN_SAMPLE_COLLECTION_DATE)
            if sampleCollectionDate:
                isValid, err = validate_sampleCollectionDate(sampleCollectionDate)
                if not isValid:
                    errors.append(err)

            sampleReceiptDate = row.get(PlanCSVcolumns.COLUMN_SAMPLE_RECEIPT_DATE)
            if sampleReceiptDate:
                isValid, err = validate_sampleReceiptDate(
                    sampleReceiptDate, sampleCollectionDate
                )
                if not isValid:
                    errors.append(err)

            population = row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_POPULATION)
            if population:
                isValid, err, _ = validate_population(population)
                if not isValid:
                    errors.append(err)

            mouseStrains = row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_MOUSE_STRAINS)
            if mouseStrains:
                isValid, err, _ = validate_mouseStrains(mouseStrains)
                if not isValid:
                    errors.append(err)

            markers = row.get(PlanCSVcolumns.COLUMN_SAMPLE_BACTERIAL_MARKER_TYPE)
            if markers:
                err, _ = validate_16s_markers(markers, PlanCSVcolumns.COLUMN_SAMPLE_BACTERIAL_MARKER_TYPE)
                if err:
                    errors.append(err)

            # TS-16335 : Validate Gender and Cancer Type
            gender = row.get(PlanCSVcolumns.COLUMN_SAMPLE_IR_GENDER)
            if gender:
                validGenders = USERINPUT["Gender"]
                if gender not in validGenders:
                    errors.append(errorDict["E004"].format(validGenders))

            cancerType = row.get(PlanCSVcolumns.COLUMN_SAMPLE_CANCER_TYPE)
            if cancerType:
                validCancerTypes = USERINPUT["CancerType"]
                if cancerType not in validCancerTypes:
                    errors.append(errorDict["E005"].format(validCancerTypes))

            # Do not process get_userInfoDict if the workflow is invalid
            if errors:
                errorMsgDict[rowCount] = errors

            if not errorMsgDict:
                # TS-16740 : The setID's UUID is not correctly populated i.e sample UUID for both DNA and RNA.
                # send the existing setIDs for comparison (relationshipType should be DNA_RNA)
                other_row_setIds = [
                    userInputDict["setid"] for userInputDict in userInput
                ]
                userInputInfoDict = get_userInfoDict(
                    row, workflowObj, rowCount, setid_suffix, other_row_setIds
                )
                userInput.append(userInputInfoDict)

        if errorMsgDict:
            if isSingleCSV:
                errorMsg = json.dumps(errorMsgDict, cls=LazyJSONEncoder)

            else:
                # csvFile = csvPlanDict.get(PlanCSVcolumns.COLUMN_SAMPLE_FILE_HEADER)
                errorMsg = json.dumps(errorMsgDict, cls=LazyJSONEncoder)

    return errorMsg, userInput


def populate_userinput_from_response(planObj, httpHost, ir_account_id, ir_user):
    # This function is mainly used get the workflow's application Type and the tag_isFactoryProvided meta data
    # NOTE:  IRU and TS terminology differs slightly.  In IRU, the column "Relation" is equivalent to TS' "RelationRole",
    # and IRU's column RelationshipType is TS' "Relation" in the JSON blob that is saved to the selectedPlugins BLOB.

    USERINPUT = {
        "user_input_url": "/rundb/api/v1/plugin/IonReporterUploader/extend/userInput/",
        "workflows": [],
    }

    iru_RelationShipURL = USERINPUT[
        "user_input_url"
    ] + "?format=json&id=%s&ir_user=%s" % (ir_account_id, ir_user)
    base_url = "http://" + "localhost" + iru_RelationShipURL
    response = requests.get(base_url)
    data = response.json()

    sampleRelationshipsTableInfo = data.get("sampleRelationshipsTableInfo", None)
    if sampleRelationshipsTableInfo:
        column_map = sampleRelationshipsTableInfo.get("column-map", None)
        for cm in column_map:
            workflow = cm.get("Workflow", "")
            tag_isFactoryProvidedWorkflow = cm.get("tag_isFactoryProvidedWorkflow", "")
            applicationType = cm.get("ApplicationType", "")
            relationshipType = cm.get("RelationshipType", "")

            USERINPUT["workflows"].append(
                {
                    "Workflow": workflow,
                    "tag_isFactoryProvidedWorkflow": tag_isFactoryProvidedWorkflow,
                    "ApplicationType": applicationType,
                    "RelationshipType": relationshipType,
                }
            )

        columns = sampleRelationshipsTableInfo.get("columns", None)
        for cm in columns:
            if cm["Name"] == "Gender":
                USERINPUT["Gender"] = json.dumps(cm["Values"])
            if cm["Name"] == "CancerType":
                USERINPUT["CancerType"] = json.dumps(cm["Values"])
            if cm["Name"] == "Population":
                USERINPUT["Population"] = json.dumps(cm["Values"])
            if cm["Name"] == "MouseStrains":
                USERINPUT["MouseStrains"] = json.dumps(cm["Values"])

    planObj.USERINPUT = USERINPUT


def call_iru_validation_api(host, iruSelectedPlugins_output, csvFile, ir_user):
    # perform all the required validation by IRU API call
    errorMsgDict = {}
    iru_validation_errMsg = []

    url = "/rundb/api/v1/plugin/IonReporterUploader/extend/wValidateUserInput/"
    accountId = iruSelectedPlugins_output["userInput"]["accountId"]

    base_url = (
        "http://" + "localhost" + url + "?id=%s&ir_user=%s" % (accountId, ir_user)
    )

    userInputInfo = iruSelectedPlugins_output["userInput"]
    response = requests.post(
        base_url, data=json.dumps(userInputInfo, cls=LazyJSONEncoder)
    )
    response = response.json()
    iruValidationResults = response.get("validationResults", "")
    if response and "validationResults" not in response:
        logger.debug(response)
        iru_validation_errMsg = i18n_errors.fatal_internalerror_during_processing("IRU")
    else:
        for result in iruValidationResults:
            error_key = "%s" % (result["row"])
            if result["errors"]:
                errorMsgDict[error_key] = result["errors"]
            if result["warnings"]:
                errorMsgDict[error_key] = result["warnings"]
    if errorMsgDict:
        iru_validation_errMsg = json.dumps(errorMsgDict, cls=LazyJSONEncoder)

    return iru_validation_errMsg


def get_user_input_info_from_csv(
    samples_contents, csvPlanDict, planObj, httpHost, ir_account_id, ir_user
):
    populate_userinput_from_response(planObj, httpHost, ir_account_id, ir_user)

    errorMsg, userInput = check_selected_values(planObj, samples_contents, csvPlanDict)

    return userInput, errorMsg


def validate_iruConfig_process_userInputInfo(
    csvPlanDict, username, samples_contents, planObj, httpHost, selectedPlugins=None
):
    IR_server_in_csv = csvPlanDict.get(PlanCSVcolumns.COLUMN_IR_ACCOUNT)

    is_vcSelected = False
    if IR_server_in_csv:
        value = "IonReporterUploader"

    if selectedPlugins and "variantCaller" in list(selectedPlugins.keys()):
        is_vcSelected = True

    errorMsg = None

    plugins = {}

    try:
        selectedPlugin = Plugin.objects.filter(name=value, selected=True, active=True)[
            0
        ]
        if selectedPlugin.name == "IonReporterUploader":
            userIRConfig = get_default_or_first_IR_account_by_userName(
                username, IR_server=IR_server_in_csv
            )

        if userIRConfig:
            userInputInfo, errorMsg = get_user_input_info_from_csv(
                samples_contents,
                csvPlanDict,
                planObj,
                httpHost,
                userIRConfig["id"],
                username,
            )

            if not errorMsg:
                pluginDict = {
                    "id": selectedPlugin.id,
                    "name": selectedPlugin.name,
                    "version": selectedPlugin.version,
                    "features": ["export"],
                }
                userInputList = {
                    "accountId": userIRConfig["id"],
                    "accountName": userIRConfig["name"],
                    "isVariantCallerSelected": is_vcSelected,
                    "isVariantCallerConfigured": False,
                    "userInputInfo": userInputInfo,
                }
                pluginDict["userInput"] = userInputList

                plugins[selectedPlugin.name] = pluginDict
        else:
            errorMsg = json.dumps(
                {
                    PlanCSVcolumns.COLUMN_IR_ACCOUNT: validation.invalid_not_reachable_not_configured(
                        PlanCSVcolumns.COLUMN_IR_ACCOUNT, IR_server_in_csv
                    )
                },
                cls=LazyJSONEncoder,
            )  # "%s is not reachable or not configured." % IR_server_in_csv
    except Exception:
        logger.exception(format_exc())
        errorMsg = json.dumps(
            {"unknown": i18n_errors.fatal_internalerror_during_processing("IRU")},
            cls=LazyJSONEncoder,
        )

    if errorMsg:
        iru_validationErrors = errorMsg
    else:
        if selectedPlugins:
            selectedPlugins.update(plugins)
        else:
            selectedPlugins = plugins

        iru_validationErrors = call_iru_validation_api(
            httpHost,
            selectedPlugins["IonReporterUploader"],
            csvPlanDict.get(PlanCSVcolumns.COLUMN_SAMPLE_FILE_HEADER, ""),
            username,
        )

    return iru_validationErrors, selectedPlugins
