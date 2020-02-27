from re import search
from itertools import chain

from csir.bech32 import encode_bech32, decode_bech32
from csir.config import settings


class Reporter():
    def __init__(self, db, api, network, denom):
        self.db = db
        self.api = api
        self.network = network
        self.denom = denom

    def calculate_income_for(self, accounts, runs):
        for run in runs:
            try:
                print(f"\nReport run for {run.target_timestamp} (height {run.height})...")

                # get the accounts we should run a report for
                accounts_for_run = self._filter_accounts_for_run(accounts, run)
                count = len(accounts_for_run)

                for index, account in enumerate(accounts_for_run):
                    status_line = f"\r{account.address} ({str(index+1).rjust(len(str(count)))}/{count})"
                    print(f"{status_line} ....", end='')
                    prev_run = self.db.get_previous_run(run)
                    report = self._generate_for(account.address, run, prev_run)
                    self.db.insert_report(account.address, run, report)
                    print(f"{status_line} DONE", end='')

                self.db.run_ok(run)

                print('' if count > 0 else "Nothing to do...")

            except:
                self.db.run_error(run)
                raise

    def _filter_accounts_for_run(self, accounts, run):
        def f(account):
            # this address was first seen after this report height
            if run and account.first_seen_height > run.height:
                return False

            # already have a report at this height
            if run and self.db.get_latest_report_height_for(account.address) >= run.height:
                return False

            return True

        return list(filter(f, accounts))

    def _generate_for(self, address, run, prev_run):
        pending = self._get_pending_rewards(address, run)
        commission = self._get_pending_commission(address, run)
        withdrawals = self._get_withdrawals(address, run, prev_run)

        if settings.debug:
            print(f"\t\tPRew: {pending}, PCom: {commission}, W: {withdrawals}", end='')
        return {
            'pending_rewards': pending,
            'pending_commission': commission,
            'withdrawals': withdrawals
        }

    def _get_pending_rewards(self, address, run):
        reward_info = self.api.get_pending_rewards(address, run.height)
        reward_info = reward_info or [{ 'amount': 0, 'denom': self.denom }]

        relevant_reward = list(filter(
            lambda bal: bal['denom'] == self.denom,
            reward_info
        ))[0]

        if relevant_reward is None: return 0
        return int(relevant_reward['amount'])

    def _get_pending_commission(self, address, run):
        operator = encode_bech32('cosmosvaloper', decode_bech32(address)[1])
        validator_info = self.api.get_validator_distribution_info(operator, run.height)
        if validator_info is None or validator_info.get('val_commission') is None: return 0

        relevant_commission = list(filter(
            lambda bal: bal['denom'] == self.denom,
            validator_info['val_commission']
        ))[0]
        if relevant_commission is None: return 0

        return int(search(r'\d+', relevant_commission['amount']).group())

    def _get_withdrawals(self, address, run, prev_run):
        start_height = prev_run.height + 1 if prev_run else 1

        # TODO, when cosmos-sdk supports this, it's going to make
        #       processing accounts with a lot of transactions a LOT easier
        txs = self.api.get_transactions({
            'transfer.recipient': address,
            # 'tx.minheight': start_height,
            # 'tx.maxheight': run.height
        })

        txs = filter(
            lambda tx: tx.succeeded and \
                       tx.is_between(start_height, run.height) and \
                       tx.is_reward_disbursement_type(self.network),
            txs
        )

        return sum(map(lambda tx: tx.disbursement(address, self.denom), txs))
