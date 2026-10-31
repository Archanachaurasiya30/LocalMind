from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app import plugins, recipes, media

router = APIRouter(prefix="/extensions", tags=["extensions"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Enabled(Strict):
    enabled: bool


class Call(Strict):
    name: str = Field(max_length=64)
    arguments: dict


class Package(Strict):
    package: str = Field(max_length=16384)


def execute(function, *args):
    try:
        return function(*args)
    except (ValueError, OSError) as error:
        # Pydantic's str(error) can echo input data. Never send it to diagnostics.
        detail = "The extension request was rejected. Check its permissions and input, then retry."
        if type(error) is ValueError:
            detail = str(error)
        raise HTTPException(400, detail) from None


@router.get("/status")
def status():
    images = media.image_status()
    return {"planner": plugins.status(), "recipe": recipes.status(), "images": {
        "status": "experimental" if images["runtime_ready"] else "not-installed",
        "available": False, "location": "local", "permissions": [],
        "reason": images["reason"] + " Optional setup and jobs are in Media.",
        "evidence": "Ten-image hardware trial passed; visual quality did not. The image pilot remains opt-in."}}


@router.post("/planner-state")
def planner_state(body: Enabled):
    return execute(plugins.set_enabled, body.enabled)


@router.post("/planner-call")
def planner_call(body: Call):
    return execute(plugins.call, body.name, body.arguments)


@router.get("/recipe-export")
def recipe_export():
    return {"package": recipes.export_recipe()}


@router.post("/recipe-import")
def recipe_import(body: Package):
    return execute(recipes.import_recipe, body.package)
