import uuid
from datetime import datetime, timezone


class DemoMailReader:
    """Returns one canned message per call, for click-through demos.
    No network access, no credentials involved."""

    SCENARIOS = {
        'matched': dict(
            sender_email='sales@gulfcables.sa',
            sender_name='Gulf Cables & Electrical Industries',
            subject='RE: Quotation Request — Ref# CS-2091',
            body='Please find attached our best offer under Ref# CS-2091.',
            attachment_filename='GulfCables_Quote_Rev2.pdf',
        ),
        'no_reference': dict(
            sender_email='info@alfanar.com',
            sender_name='Alfanar Electrical Systems',
            subject='Your requested pricing',
            body='Hi, attached is the pricing you asked for.',
            attachment_filename='Quote.pdf',
        ),
        'unknown_reference': dict(
            sender_email='procurement@nesma-trading.com',
            sender_name='Nesma Trading Est.',
            subject='Quotation Ref# CS-1987',
            body='Kindly find our quotation attached for reference CS-1987.',
            attachment_filename='Nesma_Quotation.pdf',
        ),
    }

    def __init__(self, scenario='matched'):
        self.scenario = scenario

    def fetch_new_messages(self):
        base = self.SCENARIOS.get(self.scenario, self.SCENARIOS['matched'])
        return [{
            'message_id': f'demo-{uuid.uuid4()}',
            'received_at': datetime.now(timezone.utc),
            **base,
        }]