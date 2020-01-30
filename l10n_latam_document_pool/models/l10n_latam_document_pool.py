import pytz
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


def get_datetime():
    """
    Multipurpose Dominican Republic local datetime
    """

    # *-*-*-*-*- Remove this comment *-*-*-*-*-*
    # Because an user can use a distinct timezone,
    # this method ensure that DR localtime stuff like
    # auto expire Fiscal Sequence by its date works,
    # no matter server/client date.

    date_now = datetime.now()
    return pytz.timezone('America/Santo_Domingo').localize(date_now)


class L10nLatamDocumentPool(models.Model):
    _name = 'l10n_latam.document.pool'
    _description = 'Latam Document Pool'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Authorization number',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        track_visibility='onchange',
    )
    expiration_date = fields.Date(
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        track_visibility='onchange',
        default=datetime.strptime(
            str(int(str(fields.Date.today())[0:4]) + 1) + '-12-31', '%Y-%m-%d'
        ).date(),
    )
    l10n_latam_document_type_id = fields.Many2one(
        'l10n_latam.document.type',
        string='Document Type',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        track_visibility='onchange',
    )
    type = fields.Selection(
        related='l10n_latam_document_type_id.internal_type', store=True,
    )
    sequence_start = fields.Integer(
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        track_visibility='onchange',
        default=1,
        copy=False,
    )
    sequence_end = fields.Integer(
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        track_visibility='onchange',
        default=1,
        copy=False,
    )
    sequence_remaining = fields.Integer(
        string='Remaining', compute='_compute_sequence_remaining', store=True
    )
    sequence_id = fields.Many2one(
        'ir.sequence', string="Internal Sequence", copy=False,
    )
    warning_gap = fields.Integer(compute='_compute_warning_gap',)
    remaining_percentage = fields.Float(
        default=35,
        required=True,
        help="Fiscal Sequence remaining percentage to reach to start "
        "warning notifications.",
    )
    number_next_actual = fields.Integer(
        string='Next Number',
        help="Next number of this sequence",
        related='sequence_id.number_next_actual',
    )
    next_fiscal_number = fields.Char(compute='_compute_next_fiscal_number',)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('queue', 'Queue'),
            ('active', 'Active'),
            ('depleted', 'Depleted'),
            ('expired', 'Expired'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        track_visibility='onchange',
        copy=False,
    )
    can_be_queued = fields.Boolean(compute='_compute_can_be_queued',)
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.user.company_id,
        readonly=True,
        states={'draft': [('readonly', False)]},
        track_visibility='onchange',
    )

    @api.depends('state')
    def _compute_can_be_queued(self):
        for rec in self:
            rec.can_be_queued = (
                bool(
                    2
                    > self.search_count(
                        [
                            ('state', 'in', ('active', 'queue')),
                            (
                                'l10n_latam_document_type_id',
                                '=',
                                rec.l10n_latam_document_type_id.id,
                            ),
                            ('company_id', '=', rec.company_id.id),
                        ]
                    )
                    > 0
                )
                if rec.state == 'draft'
                else False
            )

    @api.depends('remaining_percentage')
    def _compute_warning_gap(self):
        for rec in self:
            rec.warning_gap = (rec.sequence_end - (rec.sequence_start - 1)) * (
                rec.remaining_percentage / 100
            )

    @api.depends('sequence_end', 'sequence_id.number_next')
    def _compute_sequence_remaining(self):
        for rec in self.filtered(lambda s: s.state == 'active'):
            if rec.sequence_id:
                rec.sequence_remaining = 0
                # Sequence remaining
                rec.sequence_remaining = (
                    rec.sequence_end - rec.sequence_id.number_next_actual + 1
                )
            else:
                rec.sequence_remaining = 0

    @api.depends(
        'l10n_latam_document_type_id.doc_code_prefix',
        'sequence_id.padding',
        'sequence_id.number_next_actual',
    )
    def _compute_next_fiscal_number(self):
        for seq in self:
            seq.next_fiscal_number = "%s%s" % (
                seq.l10n_latam_document_type_id.doc_code_prefix,
                str(seq.sequence_id.number_next_actual).zfill(seq.sequence_id.padding),
            )

    @api.onchange('l10n_latam_document_type_id')
    def _onchange_fiscal_type_id(self):
        """
        Compute draft Fiscal Sequence default sequence_start
        """
        if self.l10n_latam_document_type_id and self.state == 'draft':
            # Last active or depleted Fiscal Sequence
            fs_id = self.search(
                [
                    (
                        'l10n_latam_document_type_id',
                        '=',
                        self.l10n_latam_document_type_id.id,
                    ),
                    ('state', 'in', ('depleted', 'active')),
                    ('company_id', '=', self.company_id.id),
                ],
                order='sequence_end desc',
                limit=1,
            )
            self.sequence_start = fs_id.sequence_end + 1 if fs_id else 1

    @api.constrains('l10n_latam_document_type_id', 'state')
    def _validate_unique_active_type(self):
        """
        Validate an active sequence type uniqueness
        """
        domain = [
            ('state', '=', 'active'),
            ('l10n_latam_document_type_id', '=', self.l10n_latam_document_type_id.id),
            ('company_id', '=', self.company_id.id),
        ]
        if self.search_count(domain) > 1:
            raise ValidationError(_("Another sequence is active for this type."))

    @api.constrains(
        'sequence_start',
        'sequence_end',
        'state',
        'l10n_latam_document_type_id',
        'company_id',
    )
    def _validate_sequence_range(self):
        for rec in self.filtered(lambda s: s.state != 'cancelled'):
            if any(
                [True for value in [rec.sequence_start, rec.sequence_end] if value <= 0]
            ):
                raise ValidationError(_('Sequence values must be greater than zero.'))
            if rec.sequence_start >= rec.sequence_end:
                raise ValidationError(
                    _('End sequence must be greater than start sequence.')
                )
            domain = [
                ('sequence_start', '>=', rec.sequence_start),
                ('sequence_end', '<=', rec.sequence_end),
                (
                    'l10n_latam_document_type_id',
                    '=',
                    rec.l10n_latam_document_type_id.id,
                ),
                ('state', 'in', ('active', 'queue')),
                ('company_id', '=', rec.company_id.id),
            ]
            if self.search_count(domain) > 1:
                raise ValidationError(
                    _("You cannot use another Fiscal Sequence range.")
                )

    def unlink(self):
        for rec in self:
            if rec.sequence_id:
                rec.sequence_id.sudo().unlink()
        return super(L10nLatamDocumentPool, self).unlink()

    def copy(self, default=None):
        raise UserError(_('You cannot duplicate a Fiscal Sequence.'))

    def name_get(self):
        result = []
        for sequence in self:
            result.append(
                (
                    sequence.id,
                    "%s - %s"
                    % (sequence.name, sequence.l10n_latam_document_type_id.name),
                )
            )
        return result

    def action_view_sequence(self):
        self.ensure_one()
        sequence_id = self.sequence_id
        action = self.env.ref('base.ir_sequence_form').read()[0]
        if sequence_id:
            action['views'] = [(self.env.ref('base.sequence_view').id, 'form')]
            action['res_id'] = sequence_id.id
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action

    def action_confirm(self):
        self.ensure_one()
        msg = _(
            'Are you sure want to confirm this Fiscal Sequence? '
            'Once you confirm this Fiscal Sequence cannot be edited.'
        )
        action = self.env.ref(
            'l10n_latam_document_pool.l10n_latam_document_pool_validate_wizard_action'
        ).read()[0]
        action['context'] = {
            'default_name': msg,
            'default_l10n_latam_sequence_id': self.id,
            'action': 'confirm',
        }
        return action

    def _action_confirm(self):
        for rec in self:

            # Use DR local time
            date = get_datetime().date()

            if date >= rec.expiration_date:
                rec.state = 'expired'
            else:
                # Creates a new sequence of this Fiscal Sequence
                sequence_id = self.env['ir.sequence'].create(
                    {
                        'name': _('%s %s Sequence')
                        % (rec.l10n_latam_document_type_id.name, rec.name[-9:]),
                        'implementation': 'standard',
                        'padding': 8,
                        'number_increment': 1,
                        'number_next_actual': rec.sequence_start,
                        'number_next': rec.sequence_start,
                        'use_date_range': False,
                        'company_id': rec.company_id.id,
                    }
                )
                rec.write(
                    {'state': 'active', 'sequence_id': sequence_id.id, }
                )

    def action_cancel(self):
        self.ensure_one()
        msg = _(
            'Are you sure want to cancel this Fiscal Sequence? '
            'Once you cancel this Fiscal Sequence cannot be used.'
        )
        action = self.env.ref(
            'accounting.account_fiscal_sequence_validate_wizard_action'
        ).read()[0]
        action['context'] = {
            'default_name': msg,
            'default_l10n_latam_sequence_id': self.id,
            'action': 'cancel',
        }
        return action

    def _action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'
            if rec.sequence_id:
                # *-*-*-*-*- Remove this comment *-*-*-*-*-*
                # Preserve internal sequence just for audit purpose.
                rec.sequence_id.active = False

    def action_queue(self):
        for rec in self:
            rec.state = 'queue'

    def _expire_sequences(self):
        """
        Function called from ir.cron that check all active sequence
        expiration_date and set state = expired if necessary
        """
        # Use DR local time
        date = get_datetime().date()
        l10n_latam_sequence_ids = self.search([('state', '=', 'active')])

        for seq in l10n_latam_sequence_ids.filtered(
            lambda s: date >= s.expiration_date
        ):
            seq.state = 'expired'

    def _get_queued_fiscal_sequence(self):
        l10n_latam_sequence_id = self.search(
            [
                ('state', '=', 'queue'),
                (
                    'l10n_latam_document_type_id',
                    '=',
                    self.l10n_latam_document_type_id.id,
                ),
                ('company_id', '=', self.company_id.id),
            ],
            order='sequence_start asc',
            limit=1,
        )
        return l10n_latam_sequence_id

    def get_fiscal_number(self):

        # if not self.l10n_latam_document_type_id.l10n_latam_document_number:
        #     return False

        if self.sequence_remaining and self.sequence_remaining > 0:
            sequence_next = self.sequence_id._next()

            # After consume a sequence, evaluate if sequence
            # is depleted and set state to depleted
            if (self.sequence_remaining - 1) < 1:
                self.state = 'depleted'
                queue_sequence_id = self._get_queued_fiscal_sequence()
                if queue_sequence_id:
                    queue_sequence_id._action_confirm()

            return "%s%s" % (
                self.l10n_latam_document_type_id.doc_code_prefix,
                str(sequence_next).zfill(self.sequence_id.padding),
            )
        else:
            raise ValidationError(
                _('No Fiscal Sequence available for this type of document.')
            )