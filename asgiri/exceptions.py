
class AsgiriException(Exception):
    """Base exception for Asgiri-related errors."""
    pass

class ProtocolError(AsgiriException):
    """Exception raised for protocol-related errors."""
    pass

class ConnectionAbortedError(AsgiriException):
    """Exception raised when a connection is aborted."""
    pass