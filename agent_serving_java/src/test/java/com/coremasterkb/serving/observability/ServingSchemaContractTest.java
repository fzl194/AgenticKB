package com.coremasterkb.serving.observability;

import org.junit.jupiter.api.Test;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

class ServingSchemaContractTest {

    @Test
    void validatesLedgerWithReadOnlyQueriesAndDoesNotRunDdl() throws Exception {
        DataSource dataSource = mock(DataSource.class);
        Connection connection = mock(Connection.class);
        PreparedStatement ledgerStatement = mock(PreparedStatement.class);
        PreparedStatement versionStatement = mock(PreparedStatement.class);
        ResultSet ledgerResult = mock(ResultSet.class);
        ResultSet versionResult = mock(ResultSet.class);

        when(dataSource.getConnection()).thenReturn(connection);
        when(connection.prepareStatement(anyString()))
                .thenReturn(ledgerStatement, versionStatement);
        when(ledgerStatement.executeQuery()).thenReturn(ledgerResult);
        when(versionStatement.executeQuery()).thenReturn(versionResult);
        when(ledgerResult.next()).thenReturn(true);
        when(ledgerResult.getBoolean(1)).thenReturn(true);
        when(versionResult.next()).thenReturn(true);
        when(versionResult.getBoolean(1)).thenReturn(true);

        ServingRuntimeSchemaInitializer validator =
                new ServingRuntimeSchemaInitializer(dataSource);
        validator.ensure(dataSource, "generic");

        verify(connection, times(2)).prepareStatement(argThat(sql ->
                sql.stripLeading().toUpperCase().startsWith("SELECT")));
        verify(connection, never()).createStatement();
    }
}
