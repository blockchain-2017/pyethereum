from ethereum.utils import sha3, privtoaddr, int_to_addr, to_string, big_endian_to_int
from ethereum.parse_genesis_declaration import mk_basic_state
from ethereum import chain
from ethereum.transactions import Transaction
from ethereum.consensus_strategy import get_consensus_strategy
from ethereum.config import Env
from ethereum.ethpow import Miner
from ethereum.state_transition import apply_transaction, verify_execution_results
from ethereum.block_creation import pre_seal
from ethereum.abi import ContractTranslator
import rlp
# Initialize accounts
accounts = []
keys = []

for account_number in range(10):
    keys.append(sha3(to_string(account_number)))
    accounts.append(privtoaddr(keys[-1]))

k0, k1, k2, k3, k4, k5, k6, k7, k8, k9 = keys[:10]
a0, a1, a2, a3, a4, a5, a6, a7, a8, a9 = accounts[:10]

base_alloc = {}
for a in accounts:
    base_alloc[a] = {'balance': 10**18}
for i in range(16):
    base_alloc[int_to_addr(i)] = {'balance': 1}

# Initialize languages
languages = {}

try:
    import serpent
    languages['serpent'] = serpent
except ImportError:
    pass

from ethereum._solidity import get_solidity
_solidity = get_solidity()
if _solidity:
    languages['solidity'] = _solidity

try:
    from viper import compiler
    languages['viper'] = compiler
except ImportError:
    pass

from ethereum.abi import ContractTranslator
import types

STARTGAS = 3141592
GASPRICE = 1

from ethereum.slogging import configure_logging
config_string = ':info'
# configure_logging(config_string=config_string)

class ABIContract(object):  # pylint: disable=too-few-public-methods

    def __init__(self, _chain, _abi, address):
        self.address = address

        if isinstance(_abi, ContractTranslator):
            abi_translator = _abi
        else:
            abi_translator = ContractTranslator(_abi)

        self.translator = abi_translator

        for function_name in self.translator.function_data:
            function = self.method_factory(_chain, function_name)
            method = types.MethodType(function, self)
            setattr(self, function_name, method)

    @staticmethod
    def method_factory(test_chain, function_name):
        """ Return a proxy for calling a contract method with automatic encoding of
        argument and decoding of results.
        """

        def kall(self, *args, **kwargs):
            key = kwargs.get('sender', k0)

            result = test_chain.tx(  # pylint: disable=protected-access
                sender=key,
                to=self.address,
                value=kwargs.get('value', 0),
                data=self.translator.encode(function_name, args),
                startgas=kwargs.get('startgas', STARTGAS)
            )

            if result is False:
                return result
            if result == b'':
                return None
            o = self.translator.decode(function_name, result)
            return o[0] if len(o) == 1 else o
        return kall


class Chain(object):
    def __init__(self, alloc=None, env=None):
        self.chain = chain.Chain(mk_basic_state(base_alloc if alloc is None else alloc,
                                                None,
                                                Env() if env is None else env))
        self.cs = get_consensus_strategy(self.chain.env.config)
        self.block = self.cs.block_setup(self.chain, timestamp=self.chain.state.timestamp + 1)
        self.head_state = self.chain.state.ephemeral_clone()

    @property
    def last_tx(self):
        return self.txs_this_block[-1] if self.txs_this_block else None

    def tx(self, sender=k0, to=b'\x00' * 20, value=0, data=b'', startgas=STARTGAS, gasprice=GASPRICE):
        sender_addr = privtoaddr(sender)
        transaction = Transaction(self.head_state.get_nonce(sender_addr), gasprice, startgas,
                                  to, value, data).sign(sender)
        success, output = apply_transaction(self.head_state, transaction)
        self.block.transactions.append(transaction)
        if not success:
            return False
        return output

    def contract(self, sourcecode, args=[], sender=k0, value=0, language='evm', startgas=STARTGAS, gasprice=GASPRICE):
        if language == 'evm':
            assert len(args) == 0
            return self.tx(sender=sender, to=b'', value=value, data=sourcecode, startgas=startgas, gasprice=gasprice)
        else:
            compiler = languages[language]
            interface = compiler.mk_full_signature(sourcecode)
            ct = ContractTranslator(interface)
            code = compiler.compile(sourcecode) + (ct.encode_constructor_arguments(args) if args else b'')
            addr = self.tx(sender=sender, to=b'', value=value, data=code, startgas=startgas, gasprice=gasprice)
            return ABIContract(self, ct, addr)
        
    def mine(self, number_of_blocks=1, coinbase=a0):
        pre_seal(self.head_state, self.block)
        self.block = Miner(self.block).mine(rounds=100, start_nonce=0)
        assert self.chain.add_block(self.block)
        assert self.head_state.trie.root_hash == self.chain.state.trie.root_hash
        for i in range(1, number_of_blocks):
            b = self.cs.block_setup(self.chain, timestamp=self.chain.state.timestamp + 14)
            pre_seal(self.chain.state.ephemeral_clone(), b)
            b = Miner(b).mine(rounds=100, start_nonce=0)
            assert self.chain.add_block(b)
        self.block = self.cs.block_setup(self.chain, timestamp=self.chain.state.timestamp + 14)
        self.head_state = self.chain.state.ephemeral_clone()

    def snapshot(self):
        return self.head_state.snapshot(), len(self.block.transactions), self.block.number

    def revert(self, snapshot):
        state_snapshot, txcount, blknum = snapshot
        assert blknum == self.block.number
        self.block.transactions = self.block.transactions[:txcount]
        self.head_state.revert(state_snapshot)
