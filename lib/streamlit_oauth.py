import json
import pathlib
import time
import urllib.parse
from datetime import datetime, timedelta

import streamlit as st
from authlib.common.security import generate_token
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.requests_client import OAuth2Session
from streamlit_cookies_controller import CookieController

from lib.settings import _, settings


def get_base_url() -> str:
    session = st.runtime.get_instance()._session_mgr.list_active_sessions()[0]
    return urllib.parse.urlunparse(
        [session.client.request.protocol, session.client.request.host, "", "", "", ""]
    )


def login_button(authorization_url):
    # app_desc = _("Login is required to continue using this app.")
    # st.link_button(_("Login"), authorization_url)
    login_button = _("Click here to Login")

    container = f"""
        <a target="_self" href="{authorization_url}">
            {login_button}
        </a>
    """
    st.markdown(container, unsafe_allow_html=True)


def logout_button():
    button_text = _("Logout")

    controller = st.session_state.get("controller")
    if not controller:
        st.session_state.controller = controller = CookieController()
        time.sleep(0.5)

    with st.sidebar:
        st.write(
            _("Logged in as {username}").format(username=st.session_state.username)
        )

        if st.button(button_text):
            # st.session_state.client.revoke_token(
            #     token_endpoint, token=st.session_state.token
            # )
            st.session_state.user_data = None
            st.session_state.token = None

            controller.remove("user_data")
            controller.remove("token")
            time.sleep(1)
            st.rerun()


def group_validation(user_data, require_groups) -> bool:
    if require_groups and not set(require_groups).issubset(
        set(user_data.get("groups", []))
    ):
        st.write(
            _(
                "You are not authorized to view this page. Please contact the administrator."
            )
        )
        return False
    return True


def is_in_group(group: str) -> bool:
    return group in st.session_state.user_data.get("groups", [])


def load_user_data():
    controller = st.session_state.get("controller")
    user_data = controller and controller.get("user_data")

    if user_data:
        if isinstance(user_data, str):
            user_data = json.loads(user_data)
        st.session_state.user_data = user_data
        st.session_state.username = user_data["name"].title()
        st.session_state.token = controller.get("token")
    return user_data


def login(filename: str = None, require_groups: list[str] = None) -> bool:
    """Show login button, but return true if not configured"""
    if not settings.auth.client_id or not settings.auth.client_secret:
        return True

    controller = st.session_state.get("controller")

    if user_data := st.session_state.get("user_data"):
        if controller and not controller.get("user_data"):
            expires = datetime.now() + timedelta(days=30)
            controller.set(
                "user_data", json.dumps(st.session_state.user_data), expires=expires
            )
            controller.set("token", st.session_state.token, expires=expires)
            time.sleep(1)
        logout_button()
        return group_validation(user_data, require_groups)

    if user_data := load_user_data():
        logout_button()
        return group_validation(user_data, require_groups)

    if "token" not in st.session_state:
        st.session_state.token = None

    # if st.session_state.token is not None:
    # logout_button()
    # return True

    st_base_url = get_base_url()
    redirect_uri = st_base_url + "/"

    if filename:
        page_name = pathlib.Path(filename).stem
        redirect_uri += page_name

    try:
        code = st.query_params["code"]
        state = st.query_params["state"]

        token = st.session_state.client.fetch_token(
            settings.auth.token_endpoint,
            code=code,
            state=state,
            nonce=st.session_state.nonce,
        )
        resp = st.session_state.client.get(settings.auth.userinfo_endpoint)
        resp.raise_for_status()

        st.session_state.token = token
        st.session_state.user_data = resp.json()
        st.session_state.username = st.session_state.user_data["name"].title()

        # controller.set(
        #     "user_data",
        #     json.dumps(st.session_state.user_data),
        #     expires=datetime.now() + timedelta(days=30),
        # )
        # controller.set("token", token, expires=datetime.now() + timedelta(days=30))
        # time.sleep(1)

        logout_button()
        # rerun to reload page again and load additional funcionalities
        st.rerun()

    except (OAuthError, KeyError, AttributeError):
        # if isinstance(e, OAuthError):
        #    st.error(e)

        st.session_state.client = client = OAuth2Session(
            settings.auth.client_id,
            settings.auth.client_secret,
            scope=settings.auth.scope,
        )
        st.session_state.nonce = nonce = generate_token()
        authorization_url, state = client.create_authorization_url(
            settings.auth.authorization_endpoint, redirect_uri=redirect_uri, nonce=nonce
        )

        login_button(authorization_url)

    return False
