import base64
import datetime
import urlparse

import flask

from coprs import db
from coprs import exceptions
from coprs import forms
from coprs import helpers

from coprs.views.misc import login_required, api_login_required

from coprs.views.api_ns import api_ns

from coprs.logic import builds_logic
from coprs.logic import coprs_logic


@api_ns.route("/")
def api_home():
    """
    Render the home page of the api.
    This page provides information on how to call/use the API.
    """

    return flask.render_template("api.html")


@api_ns.route("/new/", methods=["GET", "POST"])
@login_required
def api_new_token():
    """
    Generate a new API token for the current user.
    """

    user = flask.g.user
    copr64 = base64.b64encode("copr") + "##"
    api_login = helpers.generate_api_token(
        flask.current_app.config["API_TOKEN_LENGTH"] - len(copr64))
    user.api_login = api_login
    user.api_token = helpers.generate_api_token(
        flask.current_app.config["API_TOKEN_LENGTH"])
    user.api_token_expiration = datetime.date.today() + \
        datetime.timedelta(
            days=flask.current_app.config["API_TOKEN_EXPIRATION"])

    db.session.add(user)
    db.session.commit()
    return flask.redirect(flask.url_for("api_ns.api_home"))


@api_ns.route("/coprs/<username>/new/", methods=["POST"])
@api_login_required
def api_new_copr(username):
    """
    Receive information from the user on how to create its new copr,
    check their validity and create the corresponding copr.

    :arg name: the name of the copr to add
    :arg chroots: a comma separated list of chroots to use
    :kwarg repos: a comma separated list of repository that this copr
        can use.
    :kwarg initial_pkgs: a comma separated list of initial packages to
        build in this new copr

    """

    form = forms.CoprFormFactory.create_form_cls()(csrf_enabled=False)
    httpcode = 200
    if form.validate_on_submit():
        infos = []
        try:
            copr = coprs_logic.CoprsLogic.add(
                name=form.name.data.strip(),
                repos=" ".join(form.repos.data.split()),
                user=flask.g.user,
                selected_chroots=form.selected_chroots,
                description=form.description.data,
                instructions=form.instructions.data,
                check_for_duplicates=True)
            infos.append("New project was successfully created.")

            if form.initial_pkgs.data:
                builds_logic.BuildsLogic.add(
                    user=flask.g.user,
                    pkgs=" ".join(form.initial_pkgs.data.split()),
                    copr=copr)

                infos.append("Initial packages were successfully "
                             "submitted for building.")

            output = {"output": "ok", "message": "\n".join(infos)}
            db.session.commit()
        except exceptions.DuplicateException as err:
            output = {"output": "notok", "error": err}
            httpcode = 500
            db.session.rollback()

    else:
        errormsg = "Validation error\n"
        if form.errors:
            for field, emsgs in form.errors.items():
                errormsg += "- {0}: {1}\n".format(field, "\n".join(emsgs))

        errormsg = errormsg.replace('"', "'")
        output = {"output": "notok", "error": errormsg}
        httpcode = 500

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout


@api_ns.route("/coprs/")
@api_ns.route("/coprs/<username>/")
def api_coprs_by_owner(username=None):
    """ Return the list of coprs owned by the given user.
    username is taken either from GET params or from the URL itself
    (in this order).

    :arg username: the username of the person one would like to the
        coprs of.

    """
    username = flask.request.args.get("username", None) or username
    release_tmpl = "{chroot.os_release}-{chroot.os_version}-{chroot.arch}"
    httpcode = 200
    if username:
        query = coprs_logic.CoprsLogic.get_multiple(
            flask.g.user, user_relation="owned",
            username=username, with_builds=True)

        repos = query.all()
        output = {"output": "ok", "repos": []}
        for repo in repos:
            yum_repos = {}
            for build in repo.builds:
                if build.results:
                    for chroot in repo.active_chroots:
                        release = release_tmpl.format(chroot=chroot)
                        yum_repos[release] = urlparse.urljoin(
                            build.results, release + '/')
                    break

            output["repos"].append({"name": repo.name,
                                    "additional_repos": repo.repos,
                                    "yum_repos": yum_repos,
                                    "description": repo.description,
                                    "instructions": repo.instructions})
    else:
        output = {"output": "notok", "error": "Invalid request"}
        httpcode = 500

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout

@api_ns.route("/coprs/<username>/<coprname>/detail/")
def api_coprs_by_owner_detail(username, coprname):
    """ Return detail of one project.

    :arg username: the username of the person one would like to the
        coprs of.
    :arg coprname: the name of project.

    """
    copr = coprs_logic.CoprsLogic.get(flask.g.user, username,
                                      coprname).first()
    release_tmpl = "{chroot.os_release}-{chroot.os_version}-{chroot.arch}"
    httpcode = 200
    if username and copr:
        output = {"output": "ok", "detail": {}}
        yum_repos = {}
        for build in copr.builds:
            if build.results:
                for chroot in copr.active_chroots:
                    release = release_tmpl.format(chroot=chroot)
                    yum_repos[release] = urlparse.urljoin(
                        build.results, release + '/')
                break
        output["detail"] = {"name": copr.name,
                                "additional_repos": copr.repos,
                                "yum_repos": yum_repos,
                                "description": copr.description,
                                "instructions": copr.instructions,
                                "last_modified": builds_logic.BuildsLogic.last_modified(copr)}
    else:
        output = {"output": "notok", "error": "Copr with name {0} does not exist.".format(coprname)}
        httpcode = 500

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout

@api_ns.route("/coprs/<username>/<coprname>/new_build/", methods=["POST"])
@api_login_required
def copr_new_build(username, coprname):
    form = forms.BuildForm(csrf_enabled=False)
    copr = coprs_logic.CoprsLogic.get(flask.g.user, username,
                                      coprname).first()
    httpcode = 200
    if not copr:
        output = {"output": "notok", "error":
                  "Copr with name {0} does not exist.".format(coprname)}
        httpcode = 500

    else:
        if form.validate_on_submit() and flask.g.user.can_build_in(copr):
            # we're checking authorization above for now
            build = builds_logic.BuildsLogic.add(
                user=flask.g.user,
                pkgs=form.pkgs.data.replace('\n', ' '),
                copr=copr)

            if flask.g.user.proven:
                build.memory_reqs = form.memory_reqs.data
                build.timeout = form.timeout.data

            db.session.commit()

            output = {"output": "ok",
                      "id": build.id,
                      "message": "Build was added to {0}.".format(coprname)}
        else:
            output = {"output": "notok", "error": "Invalid request"}
            httpcode = 500

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout


@api_ns.route("/coprs/build_status/<build_id>/", methods=["GET"])
@api_login_required
def build_status(build_id):
    if build_id.isdigit():
        build = builds_logic.BuildsLogic.get(build_id).first()
    else:
        build = None

    if build:
        httpcode = 200
        output = {"output": "ok",
                  "status": build.state}
    else:
        output = {"output": "notok", "error": "Invalid build"}
        httpcode = 404

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout

@api_ns.route('/coprs/<username>/<coprname>/modify/', methods=["POST"])
@api_login_required
def copr_modify(username, coprname):
    form = forms.CoprModifyForm(csrf_enabled=False)
    copr = coprs_logic.CoprsLogic.get(flask.g.user, username, coprname).first()

    if copr is None:
        output = {'output': 'notok', 'error': 'Invalid copr name or username'}
        httpcode = 500
    elif not form.validate_on_submit():
        output = {'output': 'notok', 'error': 'Invalid request'}
        httpcode = 500
    else:
        # .raw_data needs to be inspected to figure out whether the field
        # was not sent or was sent empty
        if form.description.raw_data and len(form.description.raw_data):
            copr.description = form.description.data
        if form.instructions.raw_data and len(form.instructions.raw_data):
            copr.instructions = form.instructions.data
        if form.repos.raw_data and len(form.repos.raw_data):
            copr.repos = form.repos.data

        try:
            coprs_logic.CoprsLogic.update(flask.g.user, copr)
        except (exceptions.ActionInProgressException, exceptions.InsufficientRightsException) as e:
            db.session.rollback()

            output = {'output': 'notok', 'error': str(e)}
            httpcode = 500
        else:
            db.session.commit()

            output = {'output': 'ok',
                      'description': copr.description,
                      'instructions': copr.instructions,
                      'repos': copr.repos}
            httpcode = 200

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout

@api_ns.route('/coprs/<username>/<coprname>/modify/<chrootname>/', methods=["POST"])
@api_login_required
def copr_modify_chroot(username, coprname, chrootname):
    form = forms.ModifyChrootForm(csrf_enabled=False)
    copr = coprs_logic.CoprsLogic.get(flask.g.user, username, coprname).first()
    chroot = coprs_logic.MockChrootsLogic.get_from_name(chrootname, active_only=True).first()

    if copr is None:
        output = {'output': 'notok', 'error': 'Invalid copr name or username'}
        httpcode = 500
    elif chroot is None:
        output = {'output': 'notok', 'error': 'Invalid chroot name'}
        httpcode = 500
    elif not form.validate_on_submit():
        output = {'output': 'notok', 'error': 'Invalid request'}
        httpcode = 500
    else:
        coprs_logic.CoprChrootsLogic.update_buildroot_pkgs(copr, chroot, form.buildroot_pkgs.data)
        db.session.commit()

        ch = copr.check_copr_chroot(chroot)
        output = {'output': 'ok', 'buildroot_pkgs': ch.buildroot_pkgs}
        httpcode = 200

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout

@api_ns.route('/coprs/<username>/<coprname>/detail/<chrootname>/', methods=["GET"])
def copr_chroot_details(username, coprname, chrootname):
    copr = coprs_logic.CoprsLogic.get(flask.g.user, username, coprname).first()
    chroot = coprs_logic.MockChrootsLogic.get_from_name(chrootname, active_only=True).first()

    if copr is None:
        output = {'output': 'notok', 'error': 'Invalid copr name or username'}
        httpcode = 500
    elif chroot is None:
        output = {'output': 'notok', 'error': 'Invalid chroot name'}
        httpcode = 500
    else:
        ch = copr.check_copr_chroot(chroot)
        output = {'output': 'ok', 'buildroot_pkgs': ch.buildroot_pkgs}
        httpcode = 200

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout

@api_ns.route("/coprs/search/")
@api_ns.route("/coprs/search/<project>/")
def api_coprs_search_by_project(project=None):
    """ Return the list of coprs found in search by the given text.
    project is taken either from GET params or from the URL itself
    (in this order).

    :arg project: the text one would like find for coprs.

    """
    project = flask.request.args.get("project", None) or project
    httpcode = 200
    if project:
        query = coprs_logic.CoprsLogic.get_multiple_fulltext(
            flask.g.user, project)

        repos = query.all()
        output = {"output": "ok", "users": []}
        for repo in repos:
            output["repos"].append({"username": repo.owner,
                                    "coprname"; repo.name,
                                    "description": repo.description})
    else:
        output = {"output": "notok", "error": "Invalid request"}
        httpcode = 500

    jsonout = flask.jsonify(output)
    jsonout.status_code = httpcode
    return jsonout
