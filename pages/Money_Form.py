import pandas as pd
import streamlit as st

from lib.common import AmountType, Category, _, person_select, save_money_data, settings
from lib.menu import menu
from lib.streamlit_oauth import is_in_group, login


def get_money_data_message(data: dict) -> str:
    """Show message with data to be sent"""

    if data["amount"] > 0:
        verb = _("takes")
    else:
        if data["amount_type"] == AmountType.REPAYMENT:
            verb = _("paid back")
        else:
            verb = _("gets back")

    return (
        f"{data['date']}: {data['name']} {verb} {abs(data['amount']):.2f} € "
        + _("for")
        + f" {_(data['category'])}"
        + (", " + _("comment") + ": " + data["comment"] if data["comment"] else "")
    )


def send_data():
    """Send data stored in sessions state"""
    data = st.session_state.money_data

    # create dataframe
    df = pd.DataFrame(data=data, index=[0])

    save_money_data(df, message=get_money_data_message(data))

    st.session_state.money_data_sent = True


def show_form():
    # subheading with money formular
    st.write(
        _(
            "Please always use this formular whenever you take money with you, "
            "when you get money back because you bought something or when you pay back your debts."
        )
    )

    amount_type = st.radio(
        _("Take money or pay money?"),
        list(map(str, AmountType)),
        captions=[
            _("I took money or things with me"),
            _("I bought something"),
            _("I pay back my debts"),
        ],
        horizontal=True,
        help=_(
            "Select if amount is considered positive (=Debt) or negative (=Credit) on behalf of your account"
        ),
    )

    col1, col2 = st.columns(2)
    # dropdown list with names
    if "username" not in st.session_state or is_in_group("authentik Admins"):
        with col1:
            name = person_select()
    else:
        name = col1.text_input("Name", value=st.session_state.username, disabled=True)

    # input field for date
    date = col2.date_input(_("Date"), pd.Timestamp.now().date())

    col1, col2 = st.columns(2)
    amount = col1.number_input(
        _("Amount"),
        help=_("Just positive amounts here, round up in case of cents"),
        min_value=0.0,
        step=0.01,
        value=None,
        format="%0.2f",
    )

    # input field for category
    if amount_type == AmountType.REPAYMENT:
        category = col2.selectbox(_("Select category"), ["Repayment"], disabled=True)
    else:
        category = col2.selectbox(
            _("Select category"),
            list(map(str, Category)),
            help=_("If you select Other, please specify details in comment field"),
            format_func=_,
        )

    # input field for comment
    comment = st.text_input(
        _("Comment"),
        help=_("Please just fill out if really something useful to document"),
    )

    send_button = st.button(_("Send"), type="primary")
    if send_button:
        if not amount:
            st.error(_("Please fill out the amount field"))
            return

        # create dict with data
        if amount_type in (AmountType.CREDIT, AmountType.REPAYMENT):
            amount *= -1

        data = {
            "name": name,
            "amount": amount,
            "amount_type": amount_type,
            "date": date,
            "category": category,
            "comment": comment,
            "timestamp": pd.Timestamp.now(),
        }

        st.session_state.money_data = data

        if amount > 0:
            info_func, change_verb = st.error, _("increased")
        else:
            info_func, change_verb = st.success, _("decreased")

        st.info(_("You are about to send following entry: "))
        info_func(f"**{get_money_data_message(data)}**")
        st.info(
            _("This means your debts will be **{change_verb}** by this amount.").format(
                change_verb=change_verb
            )
        )
        st.button(_("Confirm"), type="primary", on_click=send_data)


# Streamlit app starts here
title = _("{common_name} Money Form").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="💰")
st.title(title)

menu()

is_user_logged_in = login(__file__)
if st.session_state.get("money_data_sent"):
    st.success(
        _("Entry successfully saved")
        + ": "
        + get_money_data_message(st.session_state.money_data)
    )
    if st.button(_("New entry")):
        st.session_state.money_data_sent = False
        st.rerun()
elif is_user_logged_in:
    show_form()
