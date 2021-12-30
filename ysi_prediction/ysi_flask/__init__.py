import os
import urllib.parse
from typing import Optional

from fastapi import FastAPI, Path
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.staticfiles import StaticFiles
from flask import Flask, Markup, flash, render_template, request
from pydantic import BaseModel
from wtforms import Form, StringField
from wtforms.validators import InputRequired

from ysi_flask.fragdecomp.chemical_conversions import canonicalize_smiles
from ysi_flask.fragdecomp.fragment_decomposition import (
    FragmentError,
    draw_fragment,
    draw_mol_svg,
)
from ysi_flask.prediction import predict, return_fragment_matches

# App config.
DEBUG = True
flask_app = Flask(__name__)
flask_app.config.from_object(__name__)
flask_app.config["SECRET_KEY"] = "7d441f27d441f27567d441f2b6176a"


class ReusableForm(Form):
    name = StringField("SMILES:", [InputRequired()])


def quote(x):
    return urllib.parse.quote(x, safe="")


@flask_app.route("/", methods=["GET", "POST"])
def index():
    form = ReusableForm(request.form)
    return render_template("index.html", form=form)


@flask_app.route("/result", methods=["GET", "POST"])
def result():
    form = ReusableForm(request.form)
    smiles = request.args["name"]
    can_smiles = canonicalize_smiles(smiles)

    try:

        if not can_smiles:
            raise FragmentError

        # Here's the real prediction step. We calculated the predicted mean +/-
        # std, draw the whole molecule, and return a dataframe of the component
        # fragments.

        mean, std, outlier, frag_df, exp_mean, exp_std, exp_name = predict(can_smiles)
        svg = Markup(
            draw_mol_svg(
                can_smiles,
                figsize=(150, 150),
                color_dict=dict(zip(frag_df.index, frag_df.color)),
            )
        )

        mean = round(mean, 1)
        std = round(std, 1)

        frag_df["frag_link"] = frag_df.index
        frag_df["frag_link"] = frag_df["frag_link"].apply(quote)

        if exp_name:
            smiles += " ({})".format(exp_name)

        return render_template(
            "result.html",
            form=form,
            smiles=smiles,
            mol_svg=svg,
            mean=mean,
            std=std,
            frag_df=frag_df[frag_df["train_count"] > 0],
            outlier=outlier,
            exp_mean=exp_mean,
            exp_std=exp_std,
            frag_missing_df=frag_df[frag_df["train_count"] == 0],
        )

    except FragmentError:
        # Most likely a poorly-formed SMILES string.

        flash(
            'Error: "{}" SMILES string invalid. Please enter a valid SMILES '
            "without quotes.".format(smiles)
        )
        return render_template("base.html", form=form)

    except Exception as ex:
        # Most likely a poorly-formed SMILES string.

        if "c" not in smiles.lower():
            flash(
                'Error: Input SMILES "{}" must contain a carbon ' "atom.".format(smiles)
            )

        else:
            flash("Error: Exception occurred with input " "{0}: {1}".format(smiles, ex))

        return render_template("base.html", form=form)


@flask_app.route("/frag", methods=["GET", "POST"])
def frag():
    form = ReusableForm(request.form)
    frag_str = request.args["name"]

    color = (0.9677975592919913, 0.44127456009157356, 0.5358103155058701)
    frag_svg = Markup(draw_fragment(frag_str, color))

    # try:

    fragment_row, matches = return_fragment_matches(frag_str)
    matches["smiles_link"] = matches.SMILES.apply(quote)

    return render_template(
        "frag.html",
        form=form,
        frag_str=frag_str,
        frag_svg=frag_svg,
        fragrow=fragment_row,
        matches=matches,
    )


# FastAPI changes below
class Prediction(BaseModel):
    mean: Optional[float] = None
    std: Optional[float] = None
    outlier: Optional[bool] = None
    exp_mean: Optional[float] = None
    exp_std: Optional[float] = None
    exp_name: Optional[str] = None
    status: str


description = """This tool predicts the Yield Sooting Index of a compound
as a function of its carbon types. To use, enter a SMILES string above (or
use the drawing tool) and press submit. Experimental measurements, when
available, are also displayed."""
tags_metadata = [
    {
        "name": "predict",
        "description": "Group-contribution predictions of Yield Sooting Index (YSI)",
    },
]

apiapp = FastAPI(
    title="YSI Estimator",
    description=description,
    version="1.0",
    # terms_of_service="http://example.com/terms/",
    contact={
        "name": "Peter St. John",
        "url": "https://www.nrel.gov/research/peter-stjohn.html",
    },
    # license_info={
    #     "name": "TBD",
    #     "url": "TBD",
    # },
    openapi_tags=tags_metadata,
)
smiles_path = Path(..., title="Enter a SMILES string", example="CC1=CC(=CC(=C1)O)C")


@apiapp.get("/predict/{smiles}", response_model=Prediction, tags=["predict"])
async def api(smiles: str = smiles_path):
    try:
        can_smiles = canonicalize_smiles(smiles)
        if not can_smiles:
            raise RuntimeError

    except RuntimeError:
        return {"status": "invalid smiles"}

    try:
        mean, std, outlier, frag_df, exp_mean, exp_std, exp_name = predict(can_smiles)
    except ValueError:
        return {"status": "prediction error"}

    return {
        "mean": mean,
        "std": std,
        "outlier": outlier,
        "exp_mean": exp_mean,
        "exp_std": exp_std,
        "exp_name": exp_name,
        "status": "ok",
    }


@apiapp.get("/predict", response_model=Prediction, tags=["predict"])
async def api_with_query(smiles: str):
    results = await api(smiles)
    return results


script_dir = os.path.dirname(__file__)
apiapp.mount(
    "/client",
    StaticFiles(directory=os.path.join(script_dir, "static/client")),
    name="client",
)
apiapp.mount(
    "/static", StaticFiles(directory=os.path.join(script_dir, "static")), name="static"
)
apiapp.mount("/", WSGIMiddleware(flask_app))
app = apiapp
