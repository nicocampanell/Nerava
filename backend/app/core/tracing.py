"""
OpenTelemetry distributed tracing (optional).

Provides optional OpenTelemetry instrumentation for FastAPI and HTTP clients.
Only initializes if OTEL_ENABLED=true.

Usage:
    Set environment variables:
    - OTEL_ENABLED=true
    - OTEL_EXPORTER_ENDPOINT=http://localhost:4318/v1/traces
    
    Then import and call initialize_tracing() in main_simple.py
"""
import logging
import os

logger = logging.getLogger(__name__)

_tracing_initialized = False


def initialize_tracing() -> bool:
    """
    Initialize OpenTelemetry tracing if enabled.
    
    Returns:
        True if tracing was initialized, False otherwise
    """
    global _tracing_initialized
    
    if _tracing_initialized:
        return True
    
    otel_enabled = os.getenv("OTEL_ENABLED", "false").lower() == "true"
    
    if not otel_enabled:
        logger.debug("OpenTelemetry tracing is disabled (OTEL_ENABLED=false)")
        return False
    
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        
        # Try to import OTLP exporter
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            logger.warning(
                "OpenTelemetry OTLP exporter not available. "
                "Install with: pip install opentelemetry-exporter-otlp"
            )
            return False
        
        # Get exporter endpoint
        exporter_endpoint = os.getenv(
            "OTEL_EXPORTER_ENDPOINT",
            "http://localhost:4318/v1/traces"
        )
        
        # Create resource
        resource = Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "nerava-api"),
            "service.version": os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
        })
        
        # Create tracer provider
        tracer_provider = TracerProvider(resource=resource)
        
        # Create OTLP exporter
        otlp_exporter = OTLPSpanExporter(endpoint=exporter_endpoint)
        
        # Add span processor
        span_processor = BatchSpanProcessor(otlp_exporter)
        tracer_provider.add_span_processor(span_processor)
        
        # Set global tracer provider
        trace.set_tracer_provider(tracer_provider)
        
        _tracing_initialized = True
        logger.info(f"OpenTelemetry tracing initialized (endpoint: {exporter_endpoint})")
        
        return True
    
    except ImportError as e:
        logger.warning(f"OpenTelemetry dependencies not available: {e}")
        logger.warning("Install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry tracing: {e}", exc_info=True)
        return False


def get_tracer(name: str):
    """
    Get a tracer instance.
    
    Args:
        name: Tracer name (usually __name__)
    
    Returns:
        Tracer instance or NoOpTracer if tracing not initialized
    """
    if not _tracing_initialized:
        from opentelemetry import trace
        return trace.NoOpTracer()
    
    from opentelemetry import trace
    return trace.get_tracer(name)


def is_tracing_enabled() -> bool:
    """Check if tracing is enabled and initialized"""
    return _tracing_initialized







