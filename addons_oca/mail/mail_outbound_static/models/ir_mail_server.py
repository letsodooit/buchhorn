# Copyright 2017 LasLabs Inc.
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import re
from email.utils import parseaddr

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError
from odoo.tools import formataddr


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    smtp_from = fields.Char(
        string="Email From",
        help="Set this in order to email from a specific address."
        " If the original message's 'From' does not match with the domain"
        " whitelist then it is replaced with this value. If does match with the"
        " domain whitelist then the original message's 'From' will not change",
    )
    domain_whitelist = fields.Char(
        help="Allowed Domains list separated by commas. If there is not given"
        " SMTP server it will let us to search the proper mail server to be"
        " used to sent the messages where the message 'From' email domain"
        " match with the domain whitelist."
    )

    @api.constrains("domain_whitelist")
    def check_valid_domain_whitelist(self):
        if self.domain_whitelist:
            domains = list(self.domain_whitelist.split(","))
            for domain in domains:
                if not self._is_valid_domain(domain):
                    raise ValidationError(
                        _(
                            "%s is not a valid domain. Please define a list of"
                            " valid domains separated by comma"
                        )
                        % (domain)
                    )

    @api.constrains("smtp_from")
    def check_valid_smtp_from(self):
        if self.smtp_from:
            match = re.match(
                r"^[_a-z0-9-]+(\.[_a-z0-9-]+)*@[a-z0-9-]+(\.[a-z0-9-]+)*(\."
                r"[a-z]{2,63})$",
                self.smtp_from,
            )
            if match is None:
                raise ValidationError(_("Not a valid Email From"))

    def _is_valid_domain(self, domain_name):
        domain_regex = (
            r"(([\da-zA-Z])([_\w-]{,62})\.){,127}(([\da-zA-Z])"
            r"[_\w-]{,61})?([\da-zA-Z]\.((xn\-\-[a-zA-Z\d]+)|([a-zA-Z\d]{2,})))"
        )
        domain_regex = f"{domain_regex}$"
        valid_domain_name_regex = re.compile(domain_regex, re.IGNORECASE)
        domain_name = domain_name.lower().strip()
        return True if re.match(valid_domain_name_regex, domain_name) else False

    @api.model
    def _get_domain_whitelist(self, domain_whitelist_string):
        res = domain_whitelist_string.split(",") if domain_whitelist_string else []
        res = [item.strip() for item in res]
        return res

    def _prepare_email_message(self, message, smtp_session):
        smtp_from, smtp_to_list, message = super()._prepare_email_message(
            message, smtp_session
        )
        name_from = self._context.get("name_from")
        email_from = self._context.get("email_from")
        email_domain = self._context.get("email_domain")
        mail_server = self.browse(self._context.get("mail_server_id"))
        domain_whitelist = mail_server.domain_whitelist or tools.config.get(
            "smtp_domain_whitelist"
        )
        domain_whitelist = self._get_domain_whitelist(domain_whitelist)
        # Replace the From only if needed
        if mail_server.smtp_from and (
            not domain_whitelist or email_domain not in domain_whitelist
        ):
            email_from = formataddr((name_from, mail_server.smtp_from))
            message.replace_header("From", email_from)
            smtp_from = mail_server.smtp_from
            if not self._get_default_bounce_address():
                # then, bounce handling is disabled and we want
                # Return-Path = From
                if "Return-Path" in message:
                    message.replace_header("Return-Path", email_from)
                else:
                    message.add_header("Return-Path", email_from)
        return smtp_from, smtp_to_list, message

    @api.model
    def send_email(
        self, message, mail_server_id=None, smtp_server=None, *args, **kwargs
    ):
        # Get email_from and name_from
        if message["From"].count("<") > 1:
            split_from = message["From"].rsplit(" <", 1)
            name_from = split_from[0]
            email_from = split_from[-1].replace(">", "")
        else:
            name_from, email_from = parseaddr(message["From"])
        email_domain = email_from.split("@")[1]
        # Replicate logic from core to get mail server
        # Get proper mail server to use
        if not smtp_server and not mail_server_id:
            mail_server_id = self._get_mail_sever(email_domain)
        self = self.with_context(
            name_from=name_from,
            email_from=email_from,
            email_domain=email_domain,
            mail_server_id=mail_server_id,
        )
        return super().send_email(message, mail_server_id, smtp_server, *args, **kwargs)

    @tools.ormcache("email_domain")
    def _get_mail_sever(self, email_domain):
        """return the mail server id that match with the domain_whitelist
        If not match then return the default mail server id available one"""
        mail_server_id = None
        for item in self.sudo().search(
            [("domain_whitelist", "!=", False)], order="sequence"
        ):
            domain_whitelist = self._get_domain_whitelist(item.domain_whitelist)
            if email_domain in domain_whitelist:
                mail_server_id = item.id
                break
        if not mail_server_id:
            mail_server_id = self.sudo().search([], order="sequence", limit=1).id
        return mail_server_id

    @api.model_create_multi
    def create(self, vals_list):
        self.env.registry.clear_cache()
        return super().create(vals_list)

    def write(self, values):
        self.env.registry.clear_cache()
        return super().write(values)

    def unlink(self):
        self.env.registry.clear_cache()
        return super().unlink()
