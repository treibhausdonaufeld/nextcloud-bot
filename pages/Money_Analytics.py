import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly import express as px

from lib.common import (
    Category,
    _,
    date_select,
    get_user_banks,
    load_bank_data,
    load_money_data,
    settings,
)
from lib.menu import menu
from lib.streamlit_oauth import login


def money_chart_plot(df):
    df = df.sort_values(by="date", ascending=True)
    money_chart = px.bar(
        df,
        x="date",
        y="amount",
        color="name",
        text_auto=True,
        barmode="overlay",
        # hover_name=["name", "amount", "category"],
        hover_data={
            "name": True,
            "date": True,  # "|%B %d, %Y",
            "amount": ":.2f",
            "category": True,
            "comment": True,
        },
    )
    money_chart.update_xaxes(
        type="category",
        showgrid=True,
        ticks="outside",
        tickson="boundaries",
        categoryorder="category ascending",
    )

    return money_chart


def money_chart_category(df, split_type: bool = True):
    df = df.copy()
    df["type"] = df["amount"].apply(lambda x: "Positive" if x > 0 else "Negative")

    money_hist = px.histogram(
        df,
        x="category",
        y="amount",
        color="type" if split_type else None,
        text_auto=True,
        hover_data={"amount": ":.2f", "comment": True},
    )
    if split_type:
        df_sum = df.groupby("category")["amount"].sum().sort_values(ascending=False)
        money_hist.add_trace(
            go.Scatter(
                x=df_sum.index,
                y=df_sum.values,
                mode="markers+text",
                marker_size=13,
                textfont_size=14,
                text=df_sum.values,
                textposition="top center",
                name="Sum",
            )
        )
    return money_hist


def money_chart_group_date(df, show_type: bool = False, nbins=8):
    df = df.copy()
    df["type"] = df["amount"].apply(lambda x: "Positive" if x > 0 else "Negative")

    money_hist = px.histogram(
        df,
        x="date",
        y="amount",
        color="type" if show_type else None,
        # text_auto=True,
        hover_data={"amount": ":.2f", "comment": True, "category": True},
        text_auto=True,
        nbins=nbins,
    )
    money_hist.update_layout(bargap=0.2)
    money_hist.update_xaxes(
        showgrid=True, ticks="outside", tickson="boundaries", tickformat="%Y-%m-%d"
    )

    return money_hist


def show_money_plots(df):
    if df.empty:
        return

    df = df.copy()

    category = st.multiselect(
        _("Select Categories"),
        options=df["category"].unique(),
        default=None,
        key="selected_categories",
    )
    if category:
        df = df[df["category"].isin(category)]

    # replace column "date" with this date_column
    df["date"] = df["date"].dt.date

    col1, col2, col3 = st.columns(3)
    col1.metric(
        f":green[{_('Credit')}]", f"{df[df['amount'] > 0]['amount'].sum():,.2f} €"
    )
    col2.metric(
        f":red[{_('Expenses')}]", f"{df[df['amount'] < 0]['amount'].sum():,.2f} €"
    )
    col3.metric(f"{_('Result')}", f"{df['amount'].sum():,.2f} €")

    st.subheader(_("Money Distribution"))

    col1, col2, col3 = st.columns(3)

    show_type = col1.toggle(_("Show positive/negative"), value=True)

    limit_by = col2.radio(
        _("Filter by amount"), (_("All"), _("Income"), _("Expenses")), horizontal=True
    )
    if limit_by == _("Income"):
        df = df[df["amount"] > 0]
    elif limit_by == _("Expenses"):
        df = df[df["amount"] < 0]

    # count the number of months in df["date"]
    group_by = col3.radio(
        _("Group by"),
        (_("Week"), _("Month"), _("Quarter"), _("Year")),
        horizontal=True,
        index=1,
    )
    group_by_key = {
        _("Week"): 7,
        _("Month"): 30,
        _("Quarter"): 180,
        _("Year"): 365,
    }.get(group_by, None)
    nbins = ((df["date"].max() - df["date"].min()).days // group_by_key) + 1

    st.plotly_chart(money_chart_group_date(df, show_type=show_type, nbins=nbins))

    # tab1, tab2 = st.tabs([_("Plots"), _("Raw Data")])
    # tab1.plotly_chart(money_chart_plot(df))
    # tab2.dataframe(df)

    st.subheader(_("Money Distribution by Category"))
    st.plotly_chart(money_chart_category(df))

    # print(df[df["amount"] < 0])

    col1, col2 = st.columns(2)
    col1.plotly_chart(
        px.pie(
            df[df["amount"] > 0],
            values="amount",
            names="name",
            title=_("Credits by Name"),
            hole=0.2,
        )
    )
    df_expenses = df[df["amount"] < 0]
    df_expenses.loc[:, "amount"] = df_expenses["amount"].abs()
    col2.plotly_chart(
        px.pie(
            df_expenses,
            values="amount",
            names="name",
            title=_("Expenses by Name"),
            hole=0.2,
        )
    )


def get_bank_df():
    # add bank data to df
    df_bank = load_bank_data()
    df_creditors = df_bank[
        df_bank["name"].isin(settings.money.absteige_creditors)
    ].copy()

    df_creditors["category"] = df_creditors["name"].apply(
        lambda x: Category.BEER.value if x == "Juice Brothers" else Category.RENT.value
    )

    df_rent = df_bank.loc[
        (df_bank["amount"] > 0)
        & (df_bank["amount"] <= 20)
        & (df_bank["name"].isin(get_user_banks()))
    ].copy()
    df_rent["category"] = Category.RENT.value

    df_donations = df_bank.loc[
        (df_bank["amount"] > 20) & (df_bank["name"].isin(get_user_banks()))
    ].copy()
    df_donations["category"] = "Donations + Beer"

    df_creditors = pd.concat([df_creditors, df_rent, df_donations], ignore_index=True)

    df_creditors["date"] = pd.to_datetime(df_creditors["bookingDate"]).dt.tz_localize(
        "UTC"
    )
    df_creditors["comment"] = ""
    df_creditors = df_creditors[["date", "name", "amount", "category", "comment"]]

    # order df_creditors by date
    df_creditors = df_creditors.sort_values(by="date", ascending=False)

    # add unpaid debts
    money_data = load_money_data()
    amount_sum_by_name = money_data.dropna(subset=["name"])
    # remove Vereinskonto and Lenkerbande Shop
    amount_sum_by_name = amount_sum_by_name[
        ~amount_sum_by_name["name"].isin(["Vereinskonto", "Lenkerbande Shop"])
    ]
    amount_sum_by_name = (
        amount_sum_by_name.groupby("name")["amount"].sum().sort_values(ascending=False)
    )
    # filter out entries where amount is zero
    unpaid_debts = amount_sum_by_name[amount_sum_by_name != 0].reset_index()

    # add column date which is today date and localized to UTC
    unpaid_debts["date"] = pd.Timestamp.now(tz="UTC").normalize()
    unpaid_debts["category"] = "Unpaid Debts"
    unpaid_debts["comment"] = ""

    df_creditors = pd.concat([df_creditors, unpaid_debts], ignore_index=True)

    return df_creditors


def show_plots():
    df = get_bank_df()

    date_start, date_end = date_select(df, show_quickselect=True)

    # filter dataframe for date range
    df = df[(df["date"] >= date_start) & (df["date"] <= date_end)]

    show_money_plots(df)


# Streamlit app starts here
title = _("{common_name} Money Analytics").format(common_name=settings.name)
st.set_page_config(page_title=title, layout="wide", page_icon="🤑")
st.title(title)

menu()

is_user_logged_in = login(__file__)
if is_user_logged_in:
    show_plots()
