from alembic import op

revision = 'xxxx_perf_indices'
down_revision = None
def upgrade():
    # SQLite-safe indices
    op.create_index('ix_reward_events_merchant_time','reward_events',['merchant_id','occurred_at'], unique=False)
    op.create_index('ix_reward_events_user_time','reward_events',['user_id','occurred_at'], unique=False)
    op.create_index('ix_charge_verification_logs_time','charge_verification_logs',['created_at'], unique=False)
    op.create_index('ix_green_hour_deals_merchant','green_hour_deals',['merchant_id'], unique=False)
    op.create_index('ix_energy_rep_snapshots_user_time','energy_rep_snapshots',['user_id','calculated_at'], unique=False)
def downgrade():
    op.drop_index('ix_energy_rep_snapshots_user_time')
    op.drop_index('ix_green_hour_deals_merchant')
    op.drop_index('ix_charge_verification_logs_time')
    op.drop_index('ix_reward_events_user_time')
    op.drop_index('ix_reward_events_merchant_time')
