def withdraw(balance: int, amount: int) -> int:
    """Return a balance after withdrawal or reject an overdraft."""
    if amount > balance:
        raise ValueError("insufficient funds")
    return balance - amount
